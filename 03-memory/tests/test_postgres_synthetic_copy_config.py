import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments" / "provenance"))
sys.path.insert(0, str(ROOT / "tests" / "postgres_fixture"))
import copy_synthetic as copier


class SyntheticCopyConfigTests(unittest.TestCase):
    def test_missing_fixture_flag_and_invalid_input_never_connect(self):
        with patch.object(copier, "_connection") as connection:
            with patch.dict(os.environ, {"MEMORY_PROVENANCE_TEMPORARY": ""}):
                with self.assertRaises(PermissionError):
                    copier.copy_synthetic_session("host=localhost", "a" * 64, uuid4(), uuid4(), uuid4())
            with patch.dict(os.environ, {"MEMORY_PROVENANCE_TEMPORARY": "1"}):
                for marker in (None, "a" * 63, "z" * 64, "\ud800" * 64):
                    with self.subTest(marker=repr(marker)[:20]), self.assertRaises(ValueError):
                        copier.copy_synthetic_session("host=localhost", marker, uuid4(), uuid4(), uuid4())
                with self.assertRaises(ValueError):
                    copier.copy_synthetic_session("host=localhost", "a" * 64, "no-uuid", uuid4(), uuid4())
            connection.assert_not_called()

    def test_import_does_not_require_psycopg(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        code = "import sys; sys.path[:0] = sys.argv[1:]; import copy_synthetic; print('ok')"
        result = subprocess.run([sys.executable, "-S", "-c", code, str(ROOT / "src"),
                                 str(ROOT / "experiments" / "provenance"), str(ROOT / "tests" / "postgres_fixture")],
                                env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), b"ok")
        self.assertEqual(result.stderr, b"")


if __name__ == "__main__":
    unittest.main()
