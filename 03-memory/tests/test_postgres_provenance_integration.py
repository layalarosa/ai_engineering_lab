"""Integración exclusiva del fixture efímero; datos e identidades sintéticos."""
import contextlib
import os
import sys
import threading
import traceback
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments" / "provenance"))
try:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
except ImportError:
    psycopg = None
import postgres_lab as store

DSN = os.environ.get("MEMORY_PROVENANCE_TEST_DATABASE_URL")
ADMIN_DSN = os.environ.get("MEMORY_PROVENANCE_TEST_ADMIN_DATABASE_URL")
TEMPORARY = os.environ.get("MEMORY_PROVENANCE_TEMPORARY") == "1"


@unittest.skipUnless(psycopg is not None and DSN and ADMIN_DSN and TEMPORARY,
                     "requiere el fixture PostgreSQL temporal de procedencia")
class PostgresProvenanceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.actor = "persona-simulada"
        self.key = "perfil/idioma_preferido"
        self.lab = store.PostgresProvenanceLab(DSN, {self.actor})
        self.session = uuid4()
        self.generation = self.lab.create_session(self.actor, self.session)

    def tearDown(self):
        try:
            self.lab.delete(self.actor, self.session, self.generation)
        except LookupError:
            pass

    def command(self, value="italiano", previous=None, operation_id=None, key=None):
        command = {"generation": self.generation, "operation_id": operation_id or uuid4(),
                   "key": key or self.key, "value": value}
        if previous is not None:
            command["supersedes_statement_id"] = previous
        return command

    def confirm(self, command):
        return self.lab.confirm(self.actor, self.session, command)

    def count_rows(self):
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            return {table: conn.execute("SELECT count(*) FROM memory_provenance_lab." + table +
                                        " WHERE session_id = %s", (self.session,)).fetchone()[0]
                    for table in ("messages", "statements", "current_statements", "operations")}

    def test_statement_correction_replay_and_plain_message(self):
        self.lab.append(self.actor, self.session, self.generation, "assistant", "hola\x00😀")
        initial = self.confirm(self.command("italiano"))
        request = self.command("español\x00😀", previous=initial["statement_id"])
        corrected = self.confirm(request)
        self.assertEqual(self.confirm(request), corrected)
        self.assertEqual(corrected["supersedes_statement_id"], initial["statement_id"])
        self.assertEqual(self.lab.current(self.actor, self.session, self.generation, self.key), corrected)
        self.assertEqual(self.lab.context(self.actor, self.session, self.generation)["messages"][0]["content"], "hola\x00😀")
        self.assertEqual(self.count_rows(), {"messages": 3, "statements": 2, "current_statements": 1, "operations": 2})
        with self.assertRaises(store.ConflictError):
            self.confirm({**request, "value": "otro"})
        with self.assertRaises(store.ConflictError):
            self.confirm(self.command("alemán", previous=initial["statement_id"]))

    def test_two_competing_corrections_serialize(self):
        first = self.confirm(self.command())
        barrier = threading.Barrier(2)
        def write(value):
            command = self.command(value, previous=first["statement_id"])
            barrier.wait(timeout=10)
            try:
                return self.confirm(command)["value"]
            except store.ConflictError:
                return "conflicto"
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write, value) for value in ("alemán", "francés")]
            outcomes = [future.result(timeout=20) for future in futures]
        self.assertEqual(outcomes.count("conflicto"), 1)
        self.assertEqual(self.count_rows(), {"messages": 2, "statements": 2, "current_statements": 1, "operations": 2})

    def test_identical_simultaneous_retries_create_one_statement(self):
        command = self.command()
        barrier = threading.Barrier(2)
        def write():
            barrier.wait(timeout=10)
            return self.confirm(command)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.count_rows(), {"messages": 1, "statements": 1, "current_statements": 1, "operations": 1})

    def test_failure_after_each_write_rolls_back_transaction(self):
        for name in ("_insert_message", "_insert_statement", "_set_current", "_insert_operation"):
            original = getattr(store, name)
            def fail_after(*args):
                original(*args)
                raise psycopg.OperationalError("synthetic-secret")
            with self.subTest(stage=name), patch.object(store, name, side_effect=fail_after):
                with self.assertRaises(store.MemoryStorageError) as error:
                    self.confirm(self.command("contenido-sintetico"))
                self.assertNotIn("synthetic-secret", str(error.exception))
                self.assertNotIn("contenido-sintetico", str(error.exception))
            self.assertEqual(self.count_rows(), {"messages": 0, "statements": 0, "current_statements": 0, "operations": 0})

    def test_lost_ack_after_real_commit_replays_without_duplicate(self):
        original = store._connection
        @contextlib.contextmanager
        def lost_ack(dsn):
            with original(dsn) as conn:
                yield conn
            raise store.MemoryStorageError("Confirmación perdida simulada.")
        request = self.command()
        with patch.object(store, "_connection", side_effect=lost_ack):
            with self.assertRaises(store.MemoryStorageError):
                self.confirm(request)
        self.assertEqual(self.count_rows()["operations"], 1)
        result = self.confirm(request)
        self.assertEqual(self.confirm(request), result)
        self.assertEqual(self.count_rows()["messages"], 1)

    def test_clear_chain_and_reuse_session_uuid_reject_old_generation(self):
        first = self.confirm(self.command())
        second = self.confirm(self.command("español", previous=first["statement_id"]))
        self.confirm(self.command("francés", previous=second["statement_id"]))
        old_generation = self.generation
        old_request = self.command("alemán")
        self.generation = self.lab.clear(self.actor, self.session, self.generation)
        self.assertNotEqual(self.generation, old_generation)
        self.assertEqual(self.count_rows(), {"messages": 0, "statements": 0, "current_statements": 0, "operations": 0})
        with self.assertRaises(store.ConflictError):
            self.confirm(old_request)
        self.lab.delete(self.actor, self.session, self.generation)
        self.generation = self.lab.create_session(self.actor, self.session)
        with self.assertRaises(store.ConflictError):
            self.confirm(old_request)

    def test_current_read_uses_consistent_snapshot_during_correction(self):
        first = self.confirm(self.command())
        original = store._session
        entered, release = threading.Event(), threading.Event()
        outcomes = []
        def read():
            try:
                outcomes.append(self.lab.current(self.actor, self.session, self.generation, self.key))
            except Exception as exc:
                outcomes.append(exc)
        reader = threading.Thread(target=read)
        def pause(conn, session_id, generation, *, lock):
            original(conn, session_id, generation, lock=lock)
            if threading.current_thread() is reader and not lock:
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("La prueba no liberó la lectura")
        with patch.object(store, "_session", side_effect=pause):
            reader.start()
            try:
                self.assertTrue(entered.wait(10))
                latest = self.confirm(self.command("español", previous=first["statement_id"]))
            finally:
                release.set()
                reader.join(15)
        self.assertFalse(reader.is_alive())
        self.assertEqual(outcomes, [first])
        self.assertEqual(self.lab.current(self.actor, self.session, self.generation, self.key), latest)

    def test_capacity_counts_plain_messages_and_allows_existing_replay(self):
        first_request = self.command()
        first = self.confirm(first_request)
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            with conn.transaction():
                conn.execute("SELECT 1 FROM memory_provenance_lab.sessions WHERE session_id = %s FOR UPDATE", (self.session,))
                with conn.cursor() as cursor:
                    cursor.executemany("""INSERT INTO memory_provenance_lab.messages
                        (session_id, generation, position, message_id, role, content_utf8)
                        VALUES (%s, %s, %s, %s, 'user', %s)""",
                        [(self.session, self.generation, i, uuid4(), b'x') for i in range(2, 1001)])
        with self.assertRaises(RuntimeError):
            self.confirm(self.command("nuevo", previous=first["statement_id"]))
        self.assertEqual(self.confirm(first_request), first)
        self.assertEqual(self.count_rows()["messages"], 1000)

    def test_competing_writes_for_last_slot_preserve_capacity(self):
        first = self.confirm(self.command())
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            with conn.transaction():
                conn.execute("SELECT 1 FROM memory_provenance_lab.sessions WHERE session_id = %s FOR UPDATE", (self.session,))
                with conn.cursor() as cursor:
                    cursor.executemany("""INSERT INTO memory_provenance_lab.messages
                        (session_id, generation, position, message_id, role, content_utf8)
                        VALUES (%s, %s, %s, %s, 'user', %s)""",
                        [(self.session, self.generation, i, uuid4(), b'x') for i in range(2, 1000)])
        requests = [self.command("alemán", previous=first["statement_id"]),
                    self.command("Vim", key="perfil/editor_preferido")]
        barrier = threading.Barrier(2)
        def write(command):
            barrier.wait(timeout=10)
            try:
                self.confirm(command)
                return "guardado"
            except RuntimeError as exc:
                if type(exc) is not RuntimeError:
                    raise
                return "lleno"
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write, command) for command in requests]
            outcomes = [future.result(timeout=20) for future in futures]
        self.assertCountEqual(outcomes, ["guardado", "lleno"])
        self.assertEqual(self.count_rows(), {"messages": 1000, "statements": 2,
                                             "current_statements": 1 + int(outcomes[1] == "guardado"),
                                             "operations": 2})

    def test_foreign_key_prevents_cross_key_predecessor(self):
        first = self.confirm(self.command())
        message_id = self.lab.append(self.actor, self.session, self.generation, "user", "otro")
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with conn.transaction():
                    conn.execute("""INSERT INTO memory_provenance_lab.statements
                        (statement_id, session_id, generation, key, message_id, actor, previous_id, policy_version)
                        VALUES (%s, %s, %s, 'perfil/editor_preferido', %s, %s, %s, 'simulada-v1')""",
                        (uuid4(), self.session, self.generation, message_id, self.actor, first["statement_id"]))
        self.assertEqual(self.lab.current(self.actor, self.session, self.generation, self.key), first)

    def test_missing_pointer_or_unlinked_root_fail_closed(self):
        request = self.command()
        first = self.confirm(request)
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM memory_provenance_lab.current_statements WHERE session_id = %s", (self.session,))
        with self.assertRaises(store.MemoryStorageError):
            self.lab.current(self.actor, self.session, self.generation, self.key)
        with self.assertRaises(store.MemoryStorageError):
            self.confirm(request)
        with self.assertRaises(store.MemoryStorageError):
            self.confirm(self.command("otra"))
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute("""INSERT INTO memory_provenance_lab.current_statements
                (session_id, generation, key, statement_id) VALUES (%s, %s, %s, %s)""",
                (self.session, self.generation, self.key, first["statement_id"]))
        message_id = self.lab.append(self.actor, self.session, self.generation, "user", store.preview(self.key, "otra"))
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute("""INSERT INTO memory_provenance_lab.statements
                (statement_id, session_id, generation, key, message_id, actor, policy_version)
                VALUES (%s, %s, %s, %s, %s, %s, 'simulada-v1')""",
                (uuid4(), self.session, self.generation, self.key, message_id, self.actor))
        with self.assertRaises(store.MemoryStorageError):
            self.lab.current(self.actor, self.session, self.generation, self.key)
        with self.assertRaises(store.MemoryStorageError):
            self.confirm(self.command("nueva", previous=first["statement_id"]))

    def test_runtime_roles_are_separate_and_cannot_create_or_truncate(self):
        with psycopg.connect(DSN, autocommit=True) as conn:
            privileges = conn.execute("""SELECT has_schema_privilege(current_user, 'memory_provenance_lab', 'CREATE'),
                has_table_privilege(current_user, 'memory_provenance_lab.messages', 'TRUNCATE'),
                has_schema_privilege(current_user, 'memory_lab', 'USAGE')""").fetchone()
        self.assertEqual(privileges, (False, False, False))
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            self.assertFalse(conn.execute("SELECT has_schema_privilege('memory_runtime', 'memory_provenance_lab', 'USAGE')").fetchone()[0])

    def test_reinitialization_fails_without_removing_existing_data(self):
        first = self.confirm(self.command())
        with self.assertRaises(store.MemoryStorageError):
            store.initialize(ADMIN_DSN)
        self.assertEqual(self.lab.current(self.actor, self.session, self.generation, self.key), first)

    def test_initialization_failure_rolls_back_in_fresh_temporary_database(self):
        database = "provenance_scratch_" + uuid4().hex
        created = False
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            try:
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
                created = True
                scratch_dsn = make_conninfo(ADMIN_DSN, dbname=database)
                original = store._connection
                class FailDuringSchema:
                    def __init__(self, conn):
                        self.conn = conn
                    def transaction(self):
                        return self.conn.transaction()
                    def execute(self, statement, *args):
                        result = self.conn.execute(statement, *args)
                        if "CREATE TABLE memory_provenance_lab.statements" in statement:
                            raise psycopg.OperationalError("fallo sintético")
                        return result
                @contextlib.contextmanager
                def fail_connection(dsn):
                    with original(dsn) as conn:
                        yield FailDuringSchema(conn)
                with patch.object(store, "_connection", side_effect=fail_connection):
                    with self.assertRaises(store.MemoryStorageError):
                        store.initialize(scratch_dsn)
                with psycopg.connect(scratch_dsn, autocommit=True) as conn:
                    self.assertIsNone(conn.execute("SELECT to_regnamespace('memory_provenance_lab')").fetchone()[0])
                store.initialize(scratch_dsn)
            finally:
                if created:
                    admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))

    def test_connection_failure_and_rejected_actor_are_sanitized(self):
        bad = store.PostgresProvenanceLab("host=127.0.0.1 port=1 dbname=synthetic_db user=synthetic_user password=synthetic_secret", {self.actor})
        with self.assertRaises(store.MemoryStorageError) as error:
            bad.create_session(self.actor, uuid4())
        rendered = "".join(traceback.format_exception(type(error.exception), error.exception, error.exception.__traceback__))
        for secret in ("synthetic_secret", "synthetic_user", "synthetic_db"):
            self.assertNotIn(secret, rendered)
        with self.assertRaises(PermissionError):
            self.lab.confirm("desconocido", uuid4(), self.command())


if __name__ == "__main__":
    unittest.main()
