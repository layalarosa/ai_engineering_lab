import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "experiments" / "corrections"


class HoldoutReproducibilityTests(unittest.TestCase):
    def test_frozen_experiment_reproduces_results_without_external_dependencies(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.run([sys.executable, "-S", str(FOLDER / "evaluate_holdout.py")],
                                     cwd=directory, env=env, capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stderr, b"")
        actual = json.loads(process.stdout)
        recorded = json.loads((FOLDER / "heldout-results.json").read_text(encoding="utf-8"))
        for result in (actual, recorded):
            for field in ("fecha_utc", "python", "plataforma"):
                result.pop(field)
        self.assertEqual(actual, recorded)


if __name__ == "__main__":
    unittest.main()
