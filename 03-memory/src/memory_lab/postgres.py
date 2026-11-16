"""Sesiones persistentes de laboratorio con PostgreSQL, sin estado en caché."""
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

from .memory import MAX_HISTORY_MESSAGES, _select_context, _validate_context_limits, _validate_message

MAX_DSN_BYTES = 16384


class MemoryStorageError(RuntimeError):
    """Fallo de almacenamiento sin incluir credenciales ni contenido."""


def read_database_url(environ=None) -> str:
    env = os.environ if environ is None else environ
    value, path = env.get("MEMORY_DATABASE_URL"), env.get("MEMORY_DATABASE_URL_FILE")
    if value is not None and path is not None:
        raise ValueError("Configura solo una fuente para la conexión de Memory.")
    if path is not None:
        try:
            with Path(path).open("rb") as file:
                data = file.read(MAX_DSN_BYTES + 1)
            if len(data) > MAX_DSN_BYTES:
                raise ValueError
            value = data.decode("utf-8").rstrip("\r\n")
        except (OSError, UnicodeError, ValueError):
            raise ValueError("No se pudo cargar la configuración de Memory.") from None
    return _validate_dsn(value)


def _validate_dsn(dsn):
    if not isinstance(dsn, str) or not dsn.strip():
        raise ValueError("Falta la configuración de conexión de Memory.")
    try:
        if "\x00" in dsn or len(dsn.encode("utf-8")) > MAX_DSN_BYTES:
            raise ValueError
    except (UnicodeError, ValueError):
        raise ValueError("La configuración de conexión de Memory es inválida.") from None
    return dsn


def _validate_session_id(session_id):
    if not isinstance(session_id, uuid.UUID):
        raise ValueError("El identificador de sesión debe ser un UUID.")


def _driver():
    try:
        import psycopg
    except ImportError:
        raise MemoryStorageError("Instala las dependencias de PostgreSQL de Memory.") from None
    return psycopg


@contextmanager
def _connection(dsn):
    psycopg = _driver()
    try:
        with psycopg.connect(_validate_dsn(dsn), autocommit=True, connect_timeout=10) as conn:
            yield conn
    except psycopg.Error:
        raise MemoryStorageError("No se pudo completar la operación de almacenamiento.") from None


def _transaction_settings(conn, *, read_only=False):
    isolation = "REPEATABLE READ, READ ONLY" if read_only else "READ COMMITTED"
    conn.execute("SET TRANSACTION ISOLATION LEVEL " + isolation)
    conn.execute("SET LOCAL lock_timeout = '5s'")
    conn.execute("SET LOCAL statement_timeout = '10s'")


def _require_session(conn, session_id, *, lock=False):
    sql = "SELECT 1 FROM memory_lab.sessions WHERE session_id = %s"
    if lock:
        sql += " FOR UPDATE"
    if conn.execute(sql, (session_id,)).fetchone() is None:
        raise LookupError("La sesión no existe.")


def initialize(dsn: str) -> None:
    """Inicializa una sola vez el esquema en la base elegida explícitamente."""
    with _connection(dsn) as conn:
        with conn.transaction():
            _transaction_settings(conn)
            conn.execute("CREATE SCHEMA memory_lab")
            conn.execute("CREATE TABLE memory_lab.sessions (session_id uuid PRIMARY KEY)")
            conn.execute("""CREATE TABLE memory_lab.messages (
                session_id uuid NOT NULL REFERENCES memory_lab.sessions(session_id) ON DELETE CASCADE,
                position integer NOT NULL CHECK (position BETWEEN 1 AND 1000),
                role text NOT NULL CHECK (role IN ('user', 'assistant')),
                content_utf8 bytea NOT NULL CHECK (octet_length(content_utf8) BETWEEN 1 AND 16000),
                PRIMARY KEY (session_id, position))""")


class PostgresConversationMemory:
    """Una sesión persistente; cada operación abre su propia conexión."""

    def __init__(self, dsn: str, session_id: uuid.UUID):
        self._dsn = _validate_dsn(dsn)
        _validate_session_id(session_id)
        self._session_id = session_id

    @property
    def session_id(self) -> uuid.UUID:
        return self._session_id

    @classmethod
    def create(cls, dsn: str, session_id: uuid.UUID):
        memory = cls(dsn, session_id)
        psycopg = _driver()
        with _connection(memory._dsn) as conn:
            try:
                with conn.transaction():
                    _transaction_settings(conn)
                    conn.execute("INSERT INTO memory_lab.sessions (session_id) VALUES (%s)", (session_id,))
            except psycopg.errors.UniqueViolation:
                raise ValueError("La sesión ya existe.") from None
        return memory

    @classmethod
    def open(cls, dsn: str, session_id: uuid.UUID):
        memory = cls(dsn, session_id)
        with _connection(memory._dsn) as conn:
            with conn.transaction():
                _transaction_settings(conn, read_only=True)
                _require_session(conn, session_id)
        return memory

    def append(self, role: str, content: str) -> None:
        message = _validate_message(role, content)
        with _connection(self._dsn) as conn:
            with conn.transaction():
                _transaction_settings(conn)
                _require_session(conn, self.session_id, lock=True)
                count, last = conn.execute("""SELECT count(*), COALESCE(max(position), 0)
                    FROM memory_lab.messages WHERE session_id = %s""", (self.session_id,)).fetchone()
                if count >= MAX_HISTORY_MESSAGES:
                    raise RuntimeError("La memoria de la sesión está llena.")
                conn.execute("""INSERT INTO memory_lab.messages (session_id, position, role, content_utf8)
                    VALUES (%s, %s, %s, %s)""", (self.session_id, last + 1, message.role, message.content.encode("utf-8")))

    def context(self, max_chars: int = 4000, max_messages: int = 20) -> dict:
        _validate_context_limits(max_chars, max_messages)
        with _connection(self._dsn) as conn:
            with conn.transaction():
                _transaction_settings(conn, read_only=True)
                _require_session(conn, self.session_id)
                total = conn.execute("SELECT count(*) FROM memory_lab.messages WHERE session_id = %s", (self.session_id,)).fetchone()[0]
                rows = conn.execute("""SELECT role, content_utf8 FROM memory_lab.messages
                    WHERE session_id = %s ORDER BY position DESC LIMIT %s""", (self.session_id, max_messages)).fetchall()
                if total > MAX_HISTORY_MESSAGES or len(rows) != min(total, max_messages):
                    raise MemoryStorageError("Los datos de la sesión son inconsistentes.")
                messages = []
                for role, data in reversed(rows):
                    try:
                        if not isinstance(data, (bytes, bytearray, memoryview)):
                            raise ValueError
                        content = bytes(data).decode("utf-8")
                        messages.append(_validate_message(role, content))
                    except (ValueError, UnicodeError):
                        raise MemoryStorageError("Los datos de la sesión son inválidos.") from None
                return _select_context(messages, total, max_chars, max_messages)

    def clear(self) -> None:
        with _connection(self._dsn) as conn:
            with conn.transaction():
                _transaction_settings(conn)
                _require_session(conn, self.session_id, lock=True)
                conn.execute("DELETE FROM memory_lab.messages WHERE session_id = %s", (self.session_id,))

    def delete(self) -> None:
        with _connection(self._dsn) as conn:
            with conn.transaction():
                _transaction_settings(conn)
                _require_session(conn, self.session_id, lock=True)
                conn.execute("DELETE FROM memory_lab.sessions WHERE session_id = %s", (self.session_id,))
