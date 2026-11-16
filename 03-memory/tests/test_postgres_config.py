import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from memory_lab.postgres import PostgresConversationMemory, read_database_url


class PostgresConfigTests(unittest.TestCase):
    def test_database_url_or_file(self):
        dsn = "postgresql://user:synthetic-secret@127.0.0.1/example"
        self.assertEqual(read_database_url({"MEMORY_DATABASE_URL": dsn}), dsn)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dsn"
            path.write_bytes((dsn + "\r\n").encode("utf-8"))
            self.assertEqual(read_database_url({"MEMORY_DATABASE_URL_FILE": str(path)}), dsn)
            with self.assertRaises(ValueError) as error:
                read_database_url({"MEMORY_DATABASE_URL": dsn, "MEMORY_DATABASE_URL_FILE": str(path)})
            self.assertNotIn(dsn, str(error.exception))
            path.write_bytes(b"x" * 16385)
            with self.assertRaises(ValueError):
                read_database_url({"MEMORY_DATABASE_URL_FILE": str(path)})
            path.write_bytes(b"\xff")
            with self.assertRaises(ValueError):
                read_database_url({"MEMORY_DATABASE_URL_FILE": str(path)})

    def test_invalid_dsn_does_not_expose_values_or_paths(self):
        values = [None, "", "   ", "synthetic-secret\x00", "synthetic-secret\ud800", "x" * 16385]
        for value in values:
            with self.subTest(kind=repr(value)[:25]), self.assertRaises(ValueError) as error:
                read_database_url({"MEMORY_DATABASE_URL": value})
            self.assertNotIn("synthetic-secret", str(error.exception))
        with self.assertRaises(ValueError) as error:
            read_database_url({"MEMORY_DATABASE_URL_FILE": "nonexistent-synthetic-secret"})
        self.assertNotIn("synthetic-secret", str(error.exception))

    def test_session_id_must_be_uuid(self):
        for value in (None, "123", 1, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PostgresConversationMemory("synthetic-dsn", value)
        memory = PostgresConversationMemory("synthetic-dsn", uuid.uuid4())
        with self.assertRaises(AttributeError):
            memory.session_id = uuid.uuid4()
        self.assertNotIn("synthetic-dsn", repr(memory))

    def test_memory_imports_without_optional_postgres_dependency(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        code = "from memory_lab.memory import ConversationMemory; from memory_lab.postgres import MemoryStorageError; print('ok')"
        result = subprocess.run([sys.executable, "-S", "-c", code], env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), b"ok")


if __name__ == "__main__":
    unittest.main()
