"""Copia únicamente datos sintéticos creados por el fixture temporal."""
import contextlib
import os
import sys
import threading
import time
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments" / "provenance"))
sys.path.insert(0, str(ROOT / "tests" / "postgres_fixture"))
try:
    import psycopg
    from psycopg.conninfo import make_conninfo
except ImportError:
    psycopg = None
import copy_synthetic as copier
import postgres_lab as provenance
from memory_lab.postgres import PostgresConversationMemory

SOURCE_DSN = os.environ.get("MEMORY_TEST_DATABASE_URL")
TARGET_DSN = os.environ.get("MEMORY_PROVENANCE_TEST_DATABASE_URL")
ADMIN_DSN = os.environ.get("MEMORY_TEST_ADMIN_DATABASE_URL")
COPY_DSN = os.environ.get("MEMORY_COPY_TEST_DATABASE_URL")
MARKER = os.environ.get("MEMORY_COPY_TEST_MARKER")
TEMPORARY = os.environ.get("MEMORY_PROVENANCE_TEMPORARY") == "1"


@unittest.skipUnless(psycopg is not None and all((SOURCE_DSN, TARGET_DSN, ADMIN_DSN, COPY_DSN, MARKER, TEMPORARY)),
                     "requiere el fixture PostgreSQL efímero de copia sintética")
class SyntheticCopyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.actor = "persona-simulada"
        self.source_id, self.target_id, self.generation = uuid4(), uuid4(), uuid4()
        self.source = PostgresConversationMemory.create(SOURCE_DSN, self.source_id)
        self.lab = provenance.PostgresProvenanceLab(TARGET_DSN, {self.actor})
        self.addCleanup(self.cleanup_source)
        self.addCleanup(self.cleanup_target)
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute("INSERT INTO memory_test_fixture.synthetic_sessions VALUES (%s)", (self.source_id,))

    def cleanup_source(self):
        try:
            self.source.delete()
        except LookupError:
            pass

    def cleanup_target(self):
        try:
            self.lab.delete(self.actor, self.target_id, self.generation)
        except LookupError:
            pass

    def copy(self):
        return copier.copy_synthetic_session(COPY_DSN, MARKER, self.source_id, self.target_id, self.generation)

    def rows(self, schema, session_id):
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            if schema == "memory_lab":
                return conn.execute("SELECT position, role, content_utf8 FROM memory_lab.messages WHERE session_id = %s ORDER BY position", (session_id,)).fetchall()
            return conn.execute("SELECT position, role, content_utf8 FROM memory_provenance_lab.messages WHERE session_id = %s ORDER BY position", (session_id,)).fetchall()

    def target_count(self):
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            return conn.execute("SELECT count(*) FROM memory_provenance_lab.sessions WHERE session_id = %s", (self.target_id,)).fetchone()[0]

    def test_empty_session_creates_empty_target_without_provenance(self):
        result = self.copy()
        self.assertEqual(result["messages"], [])
        self.assertEqual(self.source.context(), self.lab.context(self.actor, self.target_id, self.generation))
        self.assertIsNone(self.lab.current(self.actor, self.target_id, self.generation, "perfil/idioma_preferido"))

    def test_exact_bytes_roles_order_context_and_no_attribution(self):
        for role, content in (("user", "  inicio\x00😀  "), ("assistant", "e\u0301"), ("user", "duplicado"), ("user", "duplicado")):
            self.source.append(role, content)
        before = self.rows("memory_lab", self.source_id)
        result = self.copy()
        self.assertEqual(self.rows("memory_lab", self.source_id), before)
        self.assertEqual(self.rows("memory_provenance_lab", self.target_id), before)
        self.assertEqual([item["position"] for item in result["messages"]], [1, 2, 3, 4])
        self.assertEqual(len({item["message_id"] for item in result["messages"]}), 4)
        for chars, count in ((4000, 20), (12, 2), (20, 1)):
            with self.subTest(chars=chars, count=count):
                self.assertEqual(self.source.context(chars, count), self.lab.context(self.actor, self.target_id, self.generation, chars, count))
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            for table in ("statements", "current_statements", "operations"):
                self.assertEqual(conn.execute("SELECT count(*) FROM memory_provenance_lab." + table + " WHERE session_id = %s", (self.target_id,)).fetchone()[0], 0)

    def test_maximum_utf8_bytes_and_independent_changes_after_copy(self):
        self.source.append("user", "😀" * 4000)
        self.copy()
        self.assertEqual(self.rows("memory_provenance_lab", self.target_id), self.rows("memory_lab", self.source_id))
        self.source.append("assistant", "origen posterior")
        self.lab.append(self.actor, self.target_id, self.generation, "assistant", "destino posterior")
        self.assertEqual(self.source.context()["messages"][-1]["content"], "origen posterior")
        self.assertEqual(self.lab.context(self.actor, self.target_id, self.generation)["messages"][-1]["content"], "destino posterior")

    def test_failure_after_target_or_message_rolls_back_without_touching_source(self):
        self.source.append("user", "primero")
        self.source.append("assistant", "segundo")
        before = self.rows("memory_lab", self.source_id)
        for name in ("_create_target", "_insert_target_message"):
            original = getattr(copier, name)
            def fail_after(*args):
                original(*args)
                raise psycopg.OperationalError("synthetic-copy-secret")
            with self.subTest(stage=name), patch.object(copier, name, side_effect=fail_after):
                with self.assertRaises(copier.MemoryStorageError) as error:
                    self.copy()
                self.assertNotIn("synthetic-copy-secret", str(error.exception))
            self.assertEqual(self.target_count(), 0)
            self.assertEqual(self.rows("memory_lab", self.source_id), before)
        self.copy()
        self.assertEqual(self.rows("memory_provenance_lab", self.target_id), before)

    def test_invalid_utf8_or_position_gap_fail_without_partial_target(self):
        self.source.append("user", "válido")
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute("INSERT INTO memory_lab.messages VALUES (%s, 2, 'user', %s)", (self.source_id, b'\xffsynthetic-private'))
        with self.assertRaises(copier.MemoryStorageError) as error:
            self.copy()
        rendered = "".join(traceback.format_exception(type(error.exception), error.exception, error.exception.__traceback__))
        self.assertNotIn("synthetic-private", rendered)
        self.assertEqual(self.target_count(), 0)
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM memory_lab.messages WHERE session_id = %s AND position = 2", (self.source_id,))
            conn.execute("INSERT INTO memory_lab.messages VALUES (%s, 3, 'user', %s)", (self.source_id, b'gap'))
        before = self.rows("memory_lab", self.source_id)
        with self.assertRaises(copier.MemoryStorageError):
            self.copy()
        self.assertEqual(self.target_count(), 0)
        self.assertEqual(self.rows("memory_lab", self.source_id), before)

    def test_existing_target_is_not_overwritten(self):
        self.generation = self.lab.create_session(self.actor, self.target_id)
        self.lab.append(self.actor, self.target_id, self.generation, "user", "destino previo")
        self.source.append("user", "origen sintético")
        before_source, before_target = self.rows("memory_lab", self.source_id), self.rows("memory_provenance_lab", self.target_id)
        with self.assertRaises(copier.ConflictError):
            self.copy()
        self.assertEqual(self.rows("memory_lab", self.source_id), before_source)
        self.assertEqual(self.rows("memory_provenance_lab", self.target_id), before_target)

    def test_rejects_wrong_marker_and_unregistered_source(self):
        wrong = ("0" if MARKER[0] != "0" else "1") + MARKER[1:]
        with self.assertRaises(PermissionError):
            copier.copy_synthetic_session(COPY_DSN, wrong, self.source_id, self.target_id, self.generation)
        unregistered = PostgresConversationMemory.create(SOURCE_DSN, uuid4())
        self.addCleanup(unregistered.delete)
        with self.assertRaises(PermissionError):
            copier.copy_synthetic_session(COPY_DSN, MARKER, unregistered.session_id, self.target_id, self.generation)
        self.assertEqual(self.target_count(), 0)

    def test_post_commit_lost_ack_requires_verification_and_does_not_overwrite(self):
        self.source.append("user", "dato sintético")
        original = copier._connection
        @contextlib.contextmanager
        def lost_ack(dsn):
            with original(dsn) as conn:
                yield conn
            raise copier.MemoryStorageError("Confirmación perdida simulada.")
        with patch.object(copier, "_connection", side_effect=lost_ack):
            with self.assertRaises(copier.MemoryStorageError):
                self.copy()
        self.assertEqual(self.target_count(), 1)
        before = self.rows("memory_provenance_lab", self.target_id)
        with self.assertRaises(copier.ConflictError):
            self.copy()
        self.assertEqual(self.rows("memory_provenance_lab", self.target_id), before)
        self.assertEqual(before, self.rows("memory_lab", self.source_id))

    def test_copy_holds_source_lock_and_later_write_is_not_synchronized(self):
        self.source.append("user", "antes")
        entered, release = threading.Event(), threading.Event()
        original = copier._lock_source
        outcomes = []
        def paused(conn, source_id):
            original(conn, source_id)
            entered.set()
            if not release.wait(8):
                raise RuntimeError("La prueba no liberó la copia")
        def copy_work():
            try:
                outcomes.append(("copia", self.copy()))
            except Exception as exc:
                outcomes.append(("error_copia", exc))
        application = "memory-copy-writer-" + uuid4().hex
        writer = PostgresConversationMemory(make_conninfo(SOURCE_DSN, application_name=application), self.source_id)
        def write_work():
            try:
                writer.append("user", "después")
                outcomes.append(("escritura", None))
            except Exception as exc:
                outcomes.append(("error_escritura", exc))
        copy_thread, writer_thread = threading.Thread(target=copy_work), threading.Thread(target=write_work)
        with patch.object(copier, "_lock_source", side_effect=paused):
            copy_thread.start()
            try:
                self.assertTrue(entered.wait(8))
                writer_thread.start()
                deadline, observed = time.monotonic() + 3.5, False
                with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
                    while time.monotonic() < deadline:
                        row = conn.execute("SELECT wait_event_type FROM pg_stat_activity WHERE application_name = %s", (application,)).fetchone()
                        if row and row[0] == "Lock":
                            observed = True
                            break
                        time.sleep(0.05)
                self.assertTrue(observed, "la escritura no esperó el bloqueo de origen")
            finally:
                release.set()
                copy_thread.join(15)
                if writer_thread.ident is not None:
                    writer_thread.join(15)
        self.assertFalse(copy_thread.is_alive())
        self.assertFalse(writer_thread.is_alive())
        self.assertCountEqual([name for name, _ in outcomes], ["copia", "escritura"])
        self.assertEqual([r[2] for r in self.rows("memory_lab", self.source_id)], [b"antes", "después".encode()])
        self.assertEqual([r[2] for r in self.rows("memory_provenance_lab", self.target_id)], [b"antes"])

    def test_copy_role_has_limited_but_not_read_only_privileges(self):
        with psycopg.connect(COPY_DSN, autocommit=True) as conn:
            privileges = conn.execute("""SELECT has_table_privilege(current_user, 'memory_lab.messages', 'INSERT'),
                has_table_privilege(current_user, 'memory_lab.messages', 'DELETE'),
                has_column_privilege(current_user, 'memory_lab.sessions', 'session_id', 'UPDATE'),
                has_table_privilege(current_user, 'memory_provenance_lab.operations', 'SELECT'),
                has_table_privilege(current_user, 'memory_provenance_lab.messages', 'INSERT')""").fetchone()
        self.assertEqual(privileges, (False, False, True, False, True))


if __name__ == "__main__":
    unittest.main()
