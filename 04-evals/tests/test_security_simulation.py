import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from evals_lab import security_simulation as simulation


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write(path, values):
    path.write_bytes(b"".join(simulation._encode(value) + b"\n" for value in values))


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="security-sim-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.cases = self.folder / "cases.jsonl"
        self.fixtures = self.folder / "fixtures.jsonl"
        self.cases.write_bytes((ROOT / "examples" / "security_cases.jsonl").read_bytes())
        self.fixtures.write_bytes((ROOT / "examples" / "security_outputs.jsonl").read_bytes())
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")

    def command(self):
        return [sys.executable, "-S", "-m", "evals_lab.security_simulation", "--cases", str(self.cases), "--fixtures", str(self.fixtures)]

    def run_cli(self):
        return subprocess.run(self.command(), cwd=self.folder, env=self.env, capture_output=True, timeout=20)

    def test_http_capture_flows_into_existing_evaluator(self):
        result = self.run_cli()
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        self.assertEqual(result.stdout, self.fixtures.read_bytes())
        outputs = self.folder / "captured.jsonl"; outputs.write_bytes(result.stdout)
        reviews = ROOT / "examples" / "security_reviews.jsonl"
        cmd = [sys.executable, "-S", "-m", "evals_lab.security", "--cases", str(self.cases), "--outputs", str(outputs), "--reviews", str(reviews)]
        evaluated = subprocess.run(cmd, cwd=self.folder, env=self.env, capture_output=True, timeout=20)
        self.assertEqual((evaluated.returncode, evaluated.stderr), (0, b""))
        report = json.loads(evaluated.stdout)
        self.assertEqual((report["followed"], report["not_followed"], report["not_exposed"]), (1, 2, 1))
        self.assertNotIn(b"Bearer", result.stdout + result.stderr + evaluated.stdout + evaluated.stderr)

    def test_http_failure_is_error_and_not_a_review(self):
        fixture_rows = rows(self.fixtures)
        fixture_rows[1].update(status="error", answer=None, sources=None, sources_complete=None)
        write(self.fixtures, fixture_rows)
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        captured = [json.loads(row) for row in result.stdout.splitlines()]
        self.assertEqual(captured[1], simulation._error(fixture_rows[1]["id"]))
        self.assertEqual(len(captured), len(fixture_rows))
        outputs = self.folder / "captured.jsonl"; outputs.write_bytes(result.stdout)
        empty = self.folder / "reviews.jsonl"; empty.write_bytes(b"")
        cmd = [sys.executable, "-S", "-m", "evals_lab.security", "--cases", str(self.cases), "--outputs", str(outputs), "--reviews", str(empty)]
        evaluated = subprocess.run(cmd, cwd=self.folder, env=self.env, capture_output=True, timeout=20)
        report = json.loads(evaluated.stdout)
        self.assertEqual((evaluated.returncode, report["error"], report["followed"], report["not_followed"]), (1, 1, 0, 0))
        self.assertIsNone(report["followed_fraction"])

    def test_reject_unmatched_or_incomplete_fixtures_before_network(self):
        fixtures = rows(self.fixtures)
        for changed in (fixtures[::-1], fixtures[:-1], [dict(fixtures[0], sources_complete=False)] + fixtures[1:]):
            with self.subTest(changed=len(changed)):
                write(self.fixtures, changed)
                with patch.object(simulation, "_LocalServer", side_effect=AssertionError("should not bind")):
                    self.assertEqual(simulation.main(["--cases", str(self.cases), "--fixtures", str(self.fixtures)]), 2)

    def test_framing_and_transport_stop_without_retry(self):
        case_rows, fixture_rows = rows(self.cases), rows(self.fixtures)
        with patch.object(simulation, "capture_one", return_value=(simulation._error(case_rows[0]["id"]), True)) as capture:
            result = simulation.simulate(case_rows, fixture_rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(capture.call_count, 1)
        class BadHeaders:
            def get_all(self, key, default):
                return []
        class Response:
            headers = BadHeaders()
            status = 200
        class Connection:
            def __init__(self, *args, **kwargs): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return Response()
            def close(self): pass
        with patch.object(simulation.http.client, "HTTPConnection", Connection):
            result, stop = simulation.capture_one(case_rows[0], port=1, token="x" * 32)
        self.assertTrue(stop)
        self.assertEqual(result, simulation._error(case_rows[0]["id"]))

    def test_invalid_response_is_error_without_exposing_body(self):
        case = rows(self.cases)[0]
        class Headers:
            def get_all(self, key, default):
                return ["16"] if key == "Content-Length" else []
        class Response:
            headers = Headers()
            status = 200
            def read(self, length): return b"sensitive-secret"
        class Connection:
            def __init__(self, *args, **kwargs): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return Response()
            def close(self): pass
        with patch.object(simulation.http.client, "HTTPConnection", Connection):
            result, stop = simulation.capture_one(case, port=1, token="x" * 32)
        self.assertFalse(stop)
        self.assertEqual(result, simulation._error(case["id"]))
        self.assertNotIn("sensitive", str(result))


if __name__ == "__main__":
    unittest.main()
