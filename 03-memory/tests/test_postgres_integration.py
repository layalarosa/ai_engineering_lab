"""Integración optativa: solo usar con una base de pruebas dedicada e inicializada."""
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
try:
    import psycopg
    from psycopg.conninfo import make_conninfo
except ImportError:
    psycopg = None

from memory_lab.memory import ConversationMemory
from memory_lab import postgres as store

DSN = os.environ.get("MEMORY_TEST_DATABASE_URL")
ADMIN_DSN = os.environ.get("MEMORY_TEST_ADMIN_DATABASE_URL")


def delete_if_exists(memory):
    try:
        memory.delete()
    except LookupError:
        pass


@unittest.skipUnless(psycopg is not None and DSN, "requiere Psycopg y una base de pruebas dedicada")
class PostgresIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.memory = store.PostgresConversationMemory.create(DSN, uuid.uuid4())
        self.addCleanup(delete_if_exists, self.memory)

    def make_session(self):
        memory = store.PostgresConversationMemory.create(DSN, uuid.uuid4())
        self.addCleanup(delete_if_exists, memory)
        return memory

    def seed_messages(self, memory, amount):
        # Solo inserta en la sesión aleatoria creada por esta prueba y mantiene
        # el protocolo de bloqueo de los escritores.
        with psycopg.connect(DSN, autocommit=True) as conn:
            with conn.transaction():
                conn.execute("SELECT 1 FROM memory_lab.sessions WHERE session_id = %s FOR UPDATE", (memory.session_id,))
                with conn.cursor() as cursor:
                    cursor.executemany("""INSERT INTO memory_lab.messages (session_id, position, role, content_utf8)
                        VALUES (%s, %s, %s, %s)""", [(memory.session_id, i, "user", str(i).encode()) for i in range(1, amount + 1)])

    def test_context_matches_in_process_including_nul_and_unicode(self):
        local = ConversationMemory()
        messages = [("user", "  hola \n"), ("assistant", "😀\x00e\u0301"), ("user", "final")]
        for role, content in messages:
            local.append(role, content)
            self.memory.append(role, content)
        for chars, count in ((4000, 20), (6, 20), (100, 1), (100, 2)):
            with self.subTest(chars=chars, count=count):
                self.assertEqual(self.memory.context(chars, count), local.context(chars, count))
        with self.assertRaises(ValueError):
            self.memory.context(max_chars=4)

    def test_maximum_length_utf8_and_nul_round_trip(self):
        for content in ("😀" * 4000, "\x00" * 4000):
            with self.subTest(sample=repr(content[:2])):
                self.memory.clear()
                self.memory.append("user", content)
                self.assertEqual(self.memory.context(max_chars=4000)["messages"][0]["content"], content)

    def test_invalid_arguments_do_not_change_persisted_history(self):
        self.memory.append("user", "se conserva")
        before = self.memory.context()
        for role, content in (("system", "texto"), ("user", " " * 4), ("user", "x" * 4001),
                              ("user", "\ud800"), (None, "texto")):
            with self.subTest(role=role, content=repr(content)[:20]), self.assertRaises(ValueError):
                self.memory.append(role, content)
            self.assertEqual(self.memory.context(), before)
        for params in ({"max_chars": True}, {"max_messages": 101}, {"max_chars": 0}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.memory.context(**params)

    def test_survives_another_process_and_keeps_sessions_separate(self):
        self.memory.append("user", "primera")
        self.memory.append("assistant", "respuesta")
        other = self.make_session()
        other.append("user", "otra sesión")
        code = ("import json,os,uuid,sys; from memory_lab.postgres import PostgresConversationMemory; "
                "m=PostgresConversationMemory.open(os.environ['MEMORY_TEST_DATABASE_URL'],uuid.UUID(sys.argv[1])); "
                "print(json.dumps(m.context()))")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        completed = subprocess.run([sys.executable, "-c", code, str(self.memory.session_id)], env=env,
                                   capture_output=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), self.memory.context())
        self.assertEqual(other.context()["messages"], [{"role": "user", "content": "otra sesión"}])

    def test_demo_reopens_and_removes_its_synthetic_session(self):
        with psycopg.connect(DSN, autocommit=True) as conn:
            before = conn.execute("SELECT count(*) FROM memory_lab.sessions").fetchone()[0]
        env = os.environ.copy()
        env["MEMORY_DATABASE_URL"] = DSN
        env.pop("MEMORY_DATABASE_URL_FILE", None)
        completed = subprocess.run([sys.executable, str(ROOT / "postgres_demo.py"), "demo"], cwd=ROOT,
                                   env=env, capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {"messages": [
            {"role": "user", "content": "Uso Python"}, {"role": "assistant", "content": "Entendido."}], "omitted_count": 0})
        with psycopg.connect(DSN, autocommit=True) as conn:
            after = conn.execute("SELECT count(*) FROM memory_lab.sessions").fetchone()[0]
        self.assertEqual(after, before)

    def test_create_open_clear_delete_and_explicit_uuid_reuse(self):
        with self.assertRaises(ValueError):
            store.PostgresConversationMemory.create(DSN, self.memory.session_id)
        self.memory.append("user", "antes")
        reopened = store.PostgresConversationMemory.open(DSN, self.memory.session_id)
        reopened.clear()
        self.assertEqual(self.memory.context(), {"messages": [], "omitted_count": 0})
        reopened.append("assistant", "después")
        self.assertEqual(self.memory.context()["messages"][0]["content"], "después")
        reopened.delete()
        for action in (lambda: self.memory.context(), lambda: self.memory.append("user", "x"),
                       lambda: self.memory.clear(), lambda: self.memory.delete(),
                       lambda: store.PostgresConversationMemory.open(DSN, self.memory.session_id)):
            with self.assertRaises(LookupError):
                action()
        new = store.PostgresConversationMemory.create(DSN, self.memory.session_id)
        new.append("user", "nueva sesión con mismo ID")
        self.assertEqual(self.memory.context()["messages"][0]["content"], "nueva sesión con mismo ID")

    def test_capacity_and_simultaneous_appends(self):
        self.seed_messages(self.memory, 999)
        barrier = threading.Barrier(3)
        successes, failures = [], []
        def append(value):
            try:
                barrier.wait(timeout=10)
                self.memory.append("assistant", value)
                successes.append(value)
            except Exception as exc:
                failures.append(exc)
        workers = [threading.Thread(target=append, args=(f"writer-{i}",)) for i in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=10)
        for worker in workers:
            worker.join(timeout=20)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIs(type(failures[0]), RuntimeError)
        result = self.memory.context(max_chars=20000, max_messages=100)
        self.assertEqual(result["omitted_count"], 900)
        self.assertEqual(result["messages"][-1]["content"], successes[0])
        with psycopg.connect(DSN, autocommit=True) as conn:
            rows = conn.execute("SELECT position FROM memory_lab.messages WHERE session_id = %s ORDER BY position", (self.memory.session_id,)).fetchall()
        self.assertEqual([row[0] for row in rows], list(range(1, 1001)))

    def test_context_snapshot_remains_consistent_during_writes(self):
        for action in ("append", "clear", "delete"):
            with self.subTest(action=action):
                memory = self.make_session()
                memory.append("user", "antiguo")
                original = store._require_session
                entered, release = threading.Event(), threading.Event()
                outcomes = []
                def read():
                    try:
                        outcomes.append(memory.context())
                    except Exception as exc:
                        outcomes.append(exc)
                reader = threading.Thread(target=read)
                def pause_after_snapshot(conn, session_id, *, lock=False):
                    original(conn, session_id, lock=lock)
                    if threading.current_thread() is reader and not lock:
                        entered.set()
                        if not release.wait(10):
                            raise RuntimeError("La prueba no liberó la lectura")
                with patch.object(store, "_require_session", side_effect=pause_after_snapshot):
                    reader.start()
                    try:
                        self.assertTrue(entered.wait(10))
                        if action == "append":
                            memory.append("assistant", "nuevo")
                        elif action == "clear":
                            memory.clear()
                        else:
                            memory.delete()
                    finally:
                        release.set()
                        reader.join(timeout=15)
                self.assertFalse(reader.is_alive())
                self.assertEqual(outcomes, [{"messages": [{"role": "user", "content": "antiguo"}], "omitted_count": 0}])
                if action == "append":
                    self.assertEqual(len(memory.context()["messages"]), 2)
                elif action == "clear":
                    self.assertEqual(memory.context()["messages"], [])
                else:
                    with self.assertRaises(LookupError):
                        memory.context()

    @unittest.skipUnless(ADMIN_DSN, "requiere la conexión administrativa temporal de la prueba")
    def test_append_waiting_for_delete_does_not_recreate_session(self):
        app_name = "memory_wait_" + uuid.uuid4().hex
        memory = store.PostgresConversationMemory.open(make_conninfo(DSN, application_name=app_name), self.memory.session_id)
        outcomes = []
        def append():
            try:
                memory.append("user", "mensaje")
                outcomes.append("success")
            except Exception as exc:
                outcomes.append(exc)
        worker = threading.Thread(target=append)
        with psycopg.connect(ADMIN_DSN, autocommit=True) as holder, psycopg.connect(ADMIN_DSN, autocommit=True) as observer:
            with holder.transaction():
                holder.execute("SELECT 1 FROM memory_lab.sessions WHERE session_id = %s FOR UPDATE", (memory.session_id,))
                holder.execute("DELETE FROM memory_lab.sessions WHERE session_id = %s", (memory.session_id,))
                worker.start()
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    row = observer.execute("SELECT wait_event_type FROM pg_stat_activity WHERE application_name = %s AND state = 'active'", (app_name,)).fetchone()
                    if row and row[0] == "Lock":
                        break
                    time.sleep(0.05)
                else:
                    self.fail("La inserción no llegó a esperar el bloqueo")
            worker.join(timeout=15)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], LookupError)
        with self.assertRaises(LookupError):
            store.PostgresConversationMemory.open(DSN, self.memory.session_id)

    def test_invalid_persisted_bytes_fail_without_disclosing_content(self):
        with psycopg.connect(DSN, autocommit=True) as conn:
            with conn.transaction():
                conn.execute("SELECT 1 FROM memory_lab.sessions WHERE session_id = %s FOR UPDATE", (self.memory.session_id,))
                conn.execute("INSERT INTO memory_lab.messages VALUES (%s, 1, 'user', %s)", (self.memory.session_id, b"\xffsynthetic-secret"))
        with self.assertRaises(store.MemoryStorageError) as error:
            self.memory.context()
        self.assertNotIn("synthetic-secret", str(error.exception))

    def test_connection_error_is_sanitized(self):
        dsn = "host=127.0.0.1 port=1 dbname=missing user=synthetic-user password=synthetic-secret"
        with self.assertRaises(store.MemoryStorageError) as error:
            store.PostgresConversationMemory.open(dsn, uuid.uuid4())
        rendered = "".join(traceback.format_exception(type(error.exception), error.exception, error.exception.__traceback__))
        self.assertNotIn("synthetic-secret", rendered)
        self.assertNotIn("synthetic-user", rendered)

    def test_lock_timeout_leaves_session_unchanged(self):
        with psycopg.connect(DSN, autocommit=True) as conn:
            with conn.transaction():
                conn.execute("SELECT 1 FROM memory_lab.sessions WHERE session_id = %s FOR UPDATE", (self.memory.session_id,))
                start = time.monotonic()
                with self.assertRaises(store.MemoryStorageError):
                    self.memory.append("user", "no se guarda")
                self.assertGreaterEqual(time.monotonic() - start, 4)
                self.assertLess(time.monotonic() - start, 15)
        self.assertEqual(self.memory.context()["messages"], [])

    def test_insert_failure_rolls_back_and_hides_database_details(self):
        real_connect = psycopg.connect
        class Proxy:
            def __init__(self, connection):
                self.connection = connection
            def __enter__(self):
                self.connection.__enter__()
                return self
            def __exit__(self, *args):
                return self.connection.__exit__(*args)
            def transaction(self):
                return self.connection.transaction()
            def execute(self, query, *args, **kwargs):
                result = self.connection.execute(query, *args, **kwargs)
                if query.lstrip().startswith("INSERT INTO memory_lab.messages"):
                    raise psycopg.OperationalError("synthetic-secret contenido-privado")
                return result
        with patch.object(psycopg, "connect", side_effect=lambda *a, **kw: Proxy(real_connect(*a, **kw))):
            with self.assertRaises(store.MemoryStorageError) as error:
                self.memory.append("user", "contenido-privado")
        self.assertNotIn("synthetic-secret", str(error.exception))
        self.assertNotIn("contenido-privado", str(error.exception))
        self.assertEqual(self.memory.context()["messages"], [])

    def test_lost_commit_acknowledgement_does_not_retry(self):
        real_connect = psycopg.connect
        class Proxy:
            def __init__(self, connection):
                self.connection = connection
            def __enter__(self):
                self.connection.__enter__()
                return self
            def __exit__(self, *args):
                return self.connection.__exit__(*args)
            def execute(self, query, *args, **kwargs):
                return self.connection.execute(query, *args, **kwargs)
            @contextlib.contextmanager
            def transaction(self):
                with self.connection.transaction():
                    yield
                raise psycopg.OperationalError("synthetic-secret")
        with patch.object(psycopg, "connect", side_effect=lambda *a, **kw: Proxy(real_connect(*a, **kw))):
            with self.assertRaises(store.MemoryStorageError) as error:
                self.memory.append("user", "evento")
        self.assertNotIn("synthetic-secret", str(error.exception))
        self.assertEqual(len(self.memory.context()["messages"]), 1)
        self.memory.append("user", "evento")
        self.assertEqual(len(self.memory.context()["messages"]), 2)

    @unittest.skipUnless(ADMIN_DSN, "requiere la conexión administrativa temporal de la prueba")
    def test_initialization_does_not_modify_existing_schema(self):
        self.memory.append("user", "se conserva")
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            self.assertTrue(conn.execute("SELECT to_regnamespace('memory_lab') IS NOT NULL").fetchone()[0])
        with self.assertRaises(store.MemoryStorageError):
            store.initialize(ADMIN_DSN)
        self.assertEqual(self.memory.context()["messages"][0]["content"], "se conserva")

    def test_runtime_role_cannot_create_or_truncate_tables(self):
        with psycopg.connect(DSN, autocommit=True) as conn:
            allowed = conn.execute("""SELECT has_schema_privilege(current_user, 'memory_lab', 'CREATE'),
                has_table_privilege(current_user, 'memory_lab.messages', 'TRUNCATE')""").fetchone()
        self.assertEqual(allowed, (False, False))


if __name__ == "__main__":
    unittest.main()
