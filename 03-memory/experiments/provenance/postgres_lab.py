"""Persistencia experimental; solo para bases temporales con datos sintéticos.

La política de actores es simulada: este módulo no autentica ni autoriza usuarios.
"""
import json
from uuid import UUID, uuid4

from provenance_lab import ConflictError, _key, _uuid, preview
from memory_lab.memory import MAX_HISTORY_MESSAGES, _select_context, _validate_context_limits, _validate_message
from memory_lab.postgres import MemoryStorageError, _connection, _driver, _transaction_settings, _validate_dsn

def initialize(dsn):
    """Inicializa una base de pruebas explícita; falla si el esquema ya existe."""
    with _connection(dsn) as conn:
        with conn.transaction():
            _transaction_settings(conn)
            conn.execute("CREATE SCHEMA memory_provenance_lab")
            conn.execute("""CREATE TABLE memory_provenance_lab.schema_version (
                singleton boolean PRIMARY KEY CHECK (singleton), version integer NOT NULL CHECK (version = 1))""")
            conn.execute("INSERT INTO memory_provenance_lab.schema_version VALUES (true, 1)")
            conn.execute("""CREATE TABLE memory_provenance_lab.sessions (
                session_id uuid PRIMARY KEY, generation uuid NOT NULL, UNIQUE (session_id, generation))""")
            conn.execute("""CREATE TABLE memory_provenance_lab.messages (
                session_id uuid NOT NULL, generation uuid NOT NULL,
                position integer NOT NULL CHECK (position BETWEEN 1 AND 1000), message_id uuid NOT NULL UNIQUE,
                role text NOT NULL CHECK (role IN ('user', 'assistant')),
                content_utf8 bytea NOT NULL CHECK (octet_length(content_utf8) BETWEEN 1 AND 16000),
                PRIMARY KEY (session_id, generation, position), UNIQUE (session_id, generation, message_id),
                FOREIGN KEY (session_id, generation) REFERENCES memory_provenance_lab.sessions (session_id, generation))""")
            conn.execute("""CREATE TABLE memory_provenance_lab.statements (
                statement_id uuid PRIMARY KEY, session_id uuid NOT NULL, generation uuid NOT NULL,
                key text NOT NULL CHECK (key IN ('perfil/idioma_preferido', 'perfil/editor_preferido')),
                message_id uuid NOT NULL UNIQUE, actor text NOT NULL, previous_id uuid,
                accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(), policy_version text NOT NULL,
                UNIQUE (session_id, generation, statement_id),
                UNIQUE (session_id, generation, key, statement_id),
                FOREIGN KEY (session_id, generation, message_id)
                    REFERENCES memory_provenance_lab.messages (session_id, generation, message_id),
                FOREIGN KEY (session_id, generation, key, previous_id)
                    REFERENCES memory_provenance_lab.statements (session_id, generation, key, statement_id),
                CHECK (previous_id IS NULL OR previous_id <> statement_id))""")
            conn.execute("""CREATE TABLE memory_provenance_lab.current_statements (
                session_id uuid NOT NULL, generation uuid NOT NULL, key text NOT NULL, statement_id uuid NOT NULL,
                PRIMARY KEY (session_id, generation, key),
                FOREIGN KEY (session_id, generation, key, statement_id)
                    REFERENCES memory_provenance_lab.statements (session_id, generation, key, statement_id))""")
            conn.execute("""CREATE TABLE memory_provenance_lab.operations (
                session_id uuid NOT NULL, generation uuid NOT NULL, actor text NOT NULL, operation_id uuid NOT NULL,
                statement_id uuid NOT NULL, canonical_version integer NOT NULL CHECK (canonical_version = 1),
                PRIMARY KEY (session_id, generation, actor, operation_id),
                FOREIGN KEY (session_id, generation, statement_id)
                    REFERENCES memory_provenance_lab.statements (session_id, generation, statement_id))""")


def _version(conn):
    row = conn.execute("SELECT version FROM memory_provenance_lab.schema_version WHERE singleton = true").fetchone()
    if row != (1,):
        raise MemoryStorageError("La versión del esquema experimental es incompatible.")


def _session(conn, session_id, generation, *, lock):
    sql = "SELECT generation FROM memory_provenance_lab.sessions WHERE session_id = %s"
    if lock:
        sql += " FOR UPDATE"
    row = conn.execute(sql, (session_id,)).fetchone()
    if row is None:
        raise LookupError("Sesión inexistente.")
    if row[0] != generation:
        raise ConflictError("La generación de la sesión cambió.")


def _next_position(conn, session_id, generation):
    count, last = conn.execute("""SELECT count(*), COALESCE(max(position), 0)
        FROM memory_provenance_lab.messages WHERE session_id = %s AND generation = %s""",
        (session_id, generation)).fetchone()
    if count >= MAX_HISTORY_MESSAGES:
        raise RuntimeError("La memoria de la sesión está llena.")
    if last != count:
        raise MemoryStorageError("Los datos de la sesión son inconsistentes.")
    return last + 1


def _insert_message(conn, session_id, generation, position, message_id, role, content):
    conn.execute("""INSERT INTO memory_provenance_lab.messages
        (session_id, generation, position, message_id, role, content_utf8) VALUES (%s, %s, %s, %s, %s, %s)""",
        (session_id, generation, position, message_id, role, content.encode("utf-8")))


def _insert_statement(conn, session_id, generation, statement_id, key, message_id, actor, previous):
    conn.execute("""INSERT INTO memory_provenance_lab.statements
        (statement_id, session_id, generation, key, message_id, actor, previous_id, policy_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'simulada-v1')""",
        (statement_id, session_id, generation, key, message_id, actor, previous))


def _set_current(conn, session_id, generation, key, statement_id):
    conn.execute("""INSERT INTO memory_provenance_lab.current_statements (session_id, generation, key, statement_id)
        VALUES (%s, %s, %s, %s) ON CONFLICT (session_id, generation, key)
        DO UPDATE SET statement_id = EXCLUDED.statement_id""", (session_id, generation, key, statement_id))


def _insert_operation(conn, session_id, generation, actor, operation_id, statement_id):
    conn.execute("""INSERT INTO memory_provenance_lab.operations
        (session_id, generation, actor, operation_id, statement_id, canonical_version)
        VALUES (%s, %s, %s, %s, %s, 1)""", (session_id, generation, actor, operation_id, statement_id))


def _statement(conn, session_id, generation, statement_id):
    row = conn.execute("""SELECT s.statement_id, s.message_id, s.key, s.actor, s.previous_id,
               s.accepted_at, s.policy_version, m.role, m.content_utf8
        FROM memory_provenance_lab.statements s JOIN memory_provenance_lab.messages m
          ON (m.session_id, m.generation, m.message_id) = (s.session_id, s.generation, s.message_id)
        WHERE s.session_id = %s AND s.generation = %s AND s.statement_id = %s""",
        (session_id, generation, statement_id)).fetchone()
    if row is None:
        raise MemoryStorageError("Los datos de la sesión son inconsistentes.")
    statement, message, key, actor, previous, accepted, policy, role, data = row
    try:
        content = bytes(data).decode("utf-8")
        parsed = json.loads(content)
        if not isinstance(parsed, dict) or set(parsed) != {"clave", "valor"} or parsed["clave"] != key or role != "user":
            raise ValueError
        value = parsed["valor"]
        if preview(key, value) != content:
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise MemoryStorageError("Los datos de la sesión son inválidos.") from None
    return {"statement_id": statement, "message_id": message, "key": key, "value": value,
            "message": {"role": role, "content": content}, "simulated_actor": actor,
            "supersedes_statement_id": previous, "accepted_at": accepted.isoformat(),
            "simulated_policy_version": policy}


def _current_id(conn, session_id, generation, key):
    """Comprueba que el puntero representa una única cadena cronológica."""
    pointer = conn.execute("""SELECT statement_id FROM memory_provenance_lab.current_statements
        WHERE session_id = %s AND generation = %s AND key = %s""", (session_id, generation, key)).fetchone()
    rows = conn.execute("""SELECT s.statement_id, s.previous_id FROM memory_provenance_lab.statements s
        JOIN memory_provenance_lab.messages m
          ON (m.session_id, m.generation, m.message_id) = (s.session_id, s.generation, s.message_id)
        WHERE s.session_id = %s AND s.generation = %s AND s.key = %s ORDER BY m.position""",
        (session_id, generation, key)).fetchall()
    leaves = set()
    for statement_id, previous_id in rows:
        if previous_id is not None:
            if previous_id not in leaves:
                raise MemoryStorageError("Los datos de la sesión son inconsistentes.")
            leaves.remove(previous_id)
        leaves.add(statement_id)
    if not rows and pointer is None:
        return None
    if len(leaves) != 1 or pointer is None or pointer[0] not in leaves:
        raise MemoryStorageError("Los datos de la sesión son inconsistentes.")
    return pointer[0]


def _delete_children(conn, session_id, generation):
    for table in ("current_statements", "operations", "statements", "messages"):
        # Nombres internos constantes; la sesión y la generación son parámetros SQL.
        conn.execute("DELETE FROM memory_provenance_lab." + table + " WHERE session_id = %s AND generation = %s",
                     (session_id, generation))


class PostgresProvenanceLab:
    """Servicio experimental; los nombres de actor son elegibles por el llamador."""

    def __init__(self, dsn, simulated_allowed_actors):
        self._dsn = _validate_dsn(dsn)
        if (not isinstance(simulated_allowed_actors, (set, frozenset)) or not simulated_allowed_actors
                or any(not isinstance(a, str) or not a.strip() or len(a) > 128 or "\x00" in a
                       for a in simulated_allowed_actors)):
            raise ValueError("Política simulada inválida.")
        try:
            for actor in simulated_allowed_actors:
                actor.encode("utf-8")
        except UnicodeError:
            raise ValueError("Política simulada inválida.") from None
        self._allowed = frozenset(simulated_allowed_actors)

    def _authorize(self, actor):
        if not isinstance(actor, str) or actor not in self._allowed:
            raise PermissionError("Acceso denegado por la política simulada.")

    def create_session(self, actor, session_id):
        self._authorize(actor)
        _uuid(session_id)
        generation = uuid4()
        psycopg = _driver()
        with _connection(self._dsn) as conn:
            try:
                with conn.transaction():
                    _transaction_settings(conn)
                    _version(conn)
                    conn.execute("INSERT INTO memory_provenance_lab.sessions VALUES (%s, %s)", (session_id, generation))
            except psycopg.errors.UniqueViolation:
                raise ConflictError("La sesión ya existe.") from None
        return generation

    def append(self, actor, session_id, generation, role, content):
        self._authorize(actor)
        _uuid(session_id)
        _uuid(generation)
        message = _validate_message(role, content)
        message_id = uuid4()
        with _connection(self._dsn) as conn:
            with conn.transaction():
                _transaction_settings(conn)
                _version(conn)
                _session(conn, session_id, generation, lock=True)
                position = _next_position(conn, session_id, generation)
                _insert_message(conn, session_id, generation, position, message_id, message.role, message.content)
        return message_id

    def confirm(self, actor, session_id, command):
        self._authorize(actor)
        _uuid(session_id)
        if not isinstance(command, dict):
            raise ValueError("Solicitud inválida.")
        command = dict(command)
        required = {"generation", "operation_id", "key", "value"}
        if set(command) not in (required, required | {"supersedes_statement_id"}):
            raise ValueError("Solicitud inválida.")
        generation = _uuid(command["generation"])
        operation_id = _uuid(command["operation_id"])
        previous = _uuid(command["supersedes_statement_id"]) if "supersedes_statement_id" in command else None
        key = _key(command["key"])
        content = preview(key, command["value"])
        with _connection(self._dsn) as conn:
            with conn.transaction():
                _transaction_settings(conn)
                _version(conn)
                _session(conn, session_id, generation, lock=True)
                prior = conn.execute("""SELECT statement_id, canonical_version FROM memory_provenance_lab.operations
                    WHERE session_id = %s AND generation = %s AND actor = %s AND operation_id = %s""",
                    (session_id, generation, actor, operation_id)).fetchone()
                if prior:
                    if prior[1] != 1:
                        raise MemoryStorageError("La versión de canonicalización es incompatible.")
                    result = _statement(conn, session_id, generation, prior[0])
                    if result["simulated_actor"] != actor:
                        raise MemoryStorageError("Los datos de la sesión son inconsistentes.")
                    if (result["key"], result["message"]["content"], result["supersedes_statement_id"]) != (key, content, previous):
                        raise ConflictError("El identificador de operación ya se usó con otra solicitud.")
                    _current_id(conn, session_id, generation, key)
                    return result
                current_id = _current_id(conn, session_id, generation, key)
                if current_id != previous:
                    raise ConflictError("La versión anterior no coincide con la vigente.")
                position = _next_position(conn, session_id, generation)
                message_id, statement_id = uuid4(), uuid4()
                _insert_message(conn, session_id, generation, position, message_id, "user", content)
                _insert_statement(conn, session_id, generation, statement_id, key, message_id, actor, previous)
                _set_current(conn, session_id, generation, key, statement_id)
                _insert_operation(conn, session_id, generation, actor, operation_id, statement_id)
                result = _statement(conn, session_id, generation, statement_id)
        return result

    def current(self, actor, session_id, generation, key):
        self._authorize(actor)
        _uuid(session_id)
        _uuid(generation)
        _key(key)
        with _connection(self._dsn) as conn:
            with conn.transaction():
                _transaction_settings(conn, read_only=True)
                _version(conn)
                _session(conn, session_id, generation, lock=False)
                current_id = _current_id(conn, session_id, generation, key)
                return _statement(conn, session_id, generation, current_id) if current_id else None

    def context(self, actor, session_id, generation, max_chars=4000, max_messages=20):
        self._authorize(actor)
        _uuid(session_id)
        _uuid(generation)
        _validate_context_limits(max_chars, max_messages)
        with _connection(self._dsn) as conn:
            with conn.transaction():
                _transaction_settings(conn, read_only=True)
                _version(conn)
                _session(conn, session_id, generation, lock=False)
                total = conn.execute("""SELECT count(*) FROM memory_provenance_lab.messages
                    WHERE session_id = %s AND generation = %s""", (session_id, generation)).fetchone()[0]
                rows = conn.execute("""SELECT role, content_utf8 FROM memory_provenance_lab.messages
                    WHERE session_id = %s AND generation = %s ORDER BY position DESC LIMIT %s""",
                    (session_id, generation, max_messages)).fetchall()
                if total > MAX_HISTORY_MESSAGES or len(rows) != min(total, max_messages):
                    raise MemoryStorageError("Los datos de la sesión son inconsistentes.")
                try:
                    messages = [_validate_message(role, bytes(data).decode("utf-8")) for role, data in reversed(rows)]
                except (ValueError, TypeError, UnicodeError):
                    raise MemoryStorageError("Los datos de la sesión son inválidos.") from None
                return _select_context(messages, total, max_chars, max_messages)

    def clear(self, actor, session_id, generation):
        self._authorize(actor)
        _uuid(session_id)
        _uuid(generation)
        new_generation = uuid4()
        with _connection(self._dsn) as conn:
            with conn.transaction():
                _transaction_settings(conn)
                _version(conn)
                _session(conn, session_id, generation, lock=True)
                _delete_children(conn, session_id, generation)
                conn.execute("UPDATE memory_provenance_lab.sessions SET generation = %s WHERE session_id = %s",
                             (new_generation, session_id))
        return new_generation

    def delete(self, actor, session_id, generation):
        self._authorize(actor)
        _uuid(session_id)
        _uuid(generation)
        with _connection(self._dsn) as conn:
            with conn.transaction():
                _transaction_settings(conn)
                _version(conn)
                _session(conn, session_id, generation, lock=True)
                _delete_children(conn, session_id, generation)
                conn.execute("DELETE FROM memory_provenance_lab.sessions WHERE session_id = %s", (session_id,))
