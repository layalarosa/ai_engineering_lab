"""Copia exclusivamente sesiones sintéticas del fixture PostgreSQL efímero.

No es una herramienta de migración: la marca y el registro reducen usos
accidentales, pero no autentican un contenedor ni protegen frente a SQL directo.
"""
import os
import secrets
import string
from uuid import uuid4

from memory_lab.memory import MAX_HISTORY_MESSAGES, _validate_message
from memory_lab.postgres import MemoryStorageError, _connection, _driver, _transaction_settings
from provenance_lab import ConflictError, _uuid
from postgres_lab import _version


def _verify_fixture(conn, marker, source_id):
    if conn.execute("SELECT current_database(), current_user").fetchone() != ("memory_test", "provenance_copy_runtime"):
        raise PermissionError("La copia solo está disponible en el fixture temporal.")
    row = conn.execute("SELECT nonce FROM memory_test_fixture.identity WHERE singleton = true").fetchone()
    if row is None or not isinstance(row[0], str) or not secrets.compare_digest(row[0], marker):
        raise PermissionError("La copia solo está disponible en el fixture temporal.")
    allowed = conn.execute("SELECT 1 FROM memory_test_fixture.synthetic_sessions WHERE session_id = %s",
                           (source_id,)).fetchone()
    if allowed is None:
        raise PermissionError("La sesión no está registrada como sintética en el fixture.")


def _lock_source(conn, source_id):
    row = conn.execute("SELECT 1 FROM memory_lab.sessions WHERE session_id = %s FOR UPDATE", (source_id,)).fetchone()
    if row is None:
        raise LookupError("La sesión de origen no existe.")


def _read_source(conn, source_id):
    rows = conn.execute("""SELECT position, role, content_utf8 FROM memory_lab.messages
        WHERE session_id = %s ORDER BY position""", (source_id,)).fetchall()
    if len(rows) > MAX_HISTORY_MESSAGES:
        raise MemoryStorageError("Los datos de origen son inconsistentes.")
    result = []
    for expected, (position, role, data) in enumerate(rows, 1):
        try:
            if position != expected or not isinstance(data, (bytes, bytearray, memoryview)):
                raise ValueError
            raw = bytes(data)
            message = _validate_message(role, raw.decode("utf-8"))
            if message.content.encode("utf-8") != raw:
                raise ValueError
        except (ValueError, UnicodeError):
            raise MemoryStorageError("Los datos de origen son inválidos.") from None
        result.append((position, message.role, raw))
    return result


def _create_target(conn, target_id, target_generation):
    conn.execute("INSERT INTO memory_provenance_lab.sessions (session_id, generation) VALUES (%s, %s)",
                 (target_id, target_generation))


def _insert_target_message(conn, target_id, target_generation, position, message_id, role, raw):
    conn.execute("""INSERT INTO memory_provenance_lab.messages
        (session_id, generation, position, message_id, role, content_utf8) VALUES (%s, %s, %s, %s, %s, %s)""",
        (target_id, target_generation, position, message_id, role, raw))


def copy_synthetic_session(dsn, marker, source_id, target_id, target_generation):
    """Copia una instantánea registrada, creando el destino en la misma transacción."""
    if os.environ.get("MEMORY_PROVENANCE_TEMPORARY") != "1":
        raise PermissionError("La copia solo está disponible en el fixture temporal.")
    if (not isinstance(marker, str) or len(marker) != 64
            or any(character not in string.hexdigits for character in marker)):
        raise ValueError("Marca del fixture inválida.")
    for value in (source_id, target_id, target_generation):
        _uuid(value)
    psycopg = _driver()
    with _connection(dsn) as conn:
        try:
            with conn.transaction():
                _transaction_settings(conn)
                _verify_fixture(conn, marker, source_id)
                _version(conn)
                _lock_source(conn, source_id)
                rows = _read_source(conn, source_id)
                _create_target(conn, target_id, target_generation)
                ids = []
                for position, role, raw in rows:
                    message_id = uuid4()
                    _insert_target_message(conn, target_id, target_generation, position, message_id, role, raw)
                    ids.append({"position": position, "message_id": message_id})
        except psycopg.errors.UniqueViolation:
            raise ConflictError("No se pudo crear el destino; ya existe o hay un identificador duplicado.") from None
    return {"source_session_id": source_id, "target_session_id": target_id,
            "target_generation": target_generation, "messages": ids}
