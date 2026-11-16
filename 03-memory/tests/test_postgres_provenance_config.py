import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "provenance"))
import postgres_lab as store


class PostgresProvenanceConfigTests(unittest.TestCase):
    def test_invalid_configuration_and_policy_are_rejected_without_connection(self):
        for dsn in (None, "", " ", "\x00", "\ud800"):
            with self.subTest(dsn=repr(dsn)), self.assertRaises(ValueError):
                store.PostgresProvenanceLab(dsn, {"actor"})
        for actors in (set(), {" "}, {"a\x00b"}, {"\ud800"}, {"x" * 129}):
            with self.subTest(actors=repr(actors)), self.assertRaises(ValueError):
                store.PostgresProvenanceLab("host=localhost", actors)

    def test_unauthorized_or_invalid_command_does_not_open_connection(self):
        lab = store.PostgresProvenanceLab("host=localhost", {"permitido"})
        request = {"generation": uuid4(), "operation_id": uuid4(),
                   "key": "perfil/idioma_preferido", "value": "ejemplo"}
        with patch.object(store, "_connection") as connection:
            with self.assertRaises(PermissionError):
                lab.confirm("desconocido", uuid4(), request)
            with self.assertRaises(ValueError):
                lab.confirm("permitido", uuid4(), {**request, "actor": "permitido"})
            with self.assertRaises(ValueError):
                lab.confirm("permitido", uuid4(), {**request, "value": "\ud800"})
            connection.assert_not_called()

    def test_module_can_be_imported_without_psycopg_or_pythonpath(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        code = "import sys; sys.path.insert(0, sys.argv[1]); import postgres_lab; print('ok')"
        result = subprocess.run([sys.executable, "-S", "-c", code, str(ROOT / "experiments" / "provenance")],
                                env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), b"ok")
        self.assertEqual(result.stderr, b"")


if __name__ == "__main__":
    unittest.main()
