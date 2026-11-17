import copy
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
from evals_lab import security_orchestration_sim as sim


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="security-orchestration-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.cases_path = self.folder / "cases.jsonl"
        self.fixtures_path = self.folder / "fixtures.jsonl"
        self.cases_path.write_bytes((ROOT / "examples/security_cases.jsonl").read_bytes())
        self.fixtures_path.write_bytes((ROOT / "examples/security_outputs.jsonl").read_bytes())
        self.cases, self.fixtures, self.cases_hash, self.fixtures_hash = sim.load_inputs(self.cases_path, self.fixtures_path)

    def run_sim(self, cases=None, fixtures=None):
        return sim.run(self.cases if cases is None else cases, self.fixtures if fixtures is None else fixtures,
                       cases_sha256=self.cases_hash, fixtures_sha256=self.fixtures_hash)

    def test_five_isolated_databases_and_real_capture_against_mocks(self):
        real_connect = sim.sqlite3.connect
        with patch.object(sim.sqlite3, "connect", wraps=real_connect) as connect:
            report = self.run_sim()
        self.assertEqual((report["mode"], report["planned"], report["completed"], report["failed"], report["not_started"]),
                         ("sqlite_memory_and_http_simulated", 5, 5, 0, 0))
        self.assertEqual(connect.call_count, 5)
        self.assertTrue(all(call.args == (":memory:",) for call in connect.call_args_list))
        self.assertEqual([row["output"] for row in report["cases"]], self.fixtures)
        self.assertTrue(all(row["cleanup"] == {"api":"confirmed", "database":"confirmed"} for row in report["cases"]))
        self.assertEqual(list(sim.corpus_for(self.cases[0])), ["datos"])
        self.assertEqual(list(sim.corpus_for(self.cases[3])), ["mezcla"])
        outputs_path = self.folder / "outputs.jsonl"
        outputs_path.write_bytes(b"".join(sim._encode(row["output"]) + b"\n" for row in report["cases"]))
        reviews_path = ROOT / "examples/security_reviews.jsonl"
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")
        cmd = [sys.executable, "-S", "-m", "evals_lab.security", "--cases", str(self.cases_path),
               "--outputs", str(outputs_path), "--reviews", str(reviews_path)]
        result = subprocess.run(cmd, cwd=self.folder, env=env, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((json.loads(result.stdout)["followed"], json.loads(result.stdout)["not_followed"]), (1, 2))

    def test_corpus_mismatch_stops_before_api_and_marks_rest(self):
        with patch.object(sim, "_verify_db", side_effect=[True, True, False]), patch.object(sim, "_start_server", wraps=sim._start_server) as start:
            report = self.run_sim()
        self.assertEqual((report["completed"], report["failed"], report["not_started"]), (2, 1, 2))
        self.assertEqual(start.call_count, 2)
        failed = report["cases"][2]
        self.assertEqual((failed["stage"], failed["error"], failed["cleanup"]),
                         ("corpus_verify", "stage_failed", {"api":"not_created", "database":"confirmed"}))
        self.assertIsNone(failed["output"])

    def test_database_verification_detects_extra_model_and_changed_content(self):
        conn = sim._open_db()
        self.addCleanup(conn.close)
        documents = sim.corpus_for(self.cases[1])
        sim._populate_db(conn, documents)
        self.assertTrue(sim._verify_db(conn, documents))
        conn.execute("INSERT INTO chunks VALUES (?, ?, ?, ?)", ("other-model", "extra", 0, "unexpected"))
        self.assertFalse(sim._verify_db(conn, documents))
        conn.execute("DELETE FROM chunks WHERE model = ?", ("other-model",))
        conn.execute("UPDATE chunks SET content = ? WHERE document_id = ?", ("altered", "datos"))
        self.assertFalse(sim._verify_db(conn, documents))

    def test_capture_failure_stops_and_cleans_resources(self):
        fixtures = copy.deepcopy(self.fixtures)
        fixtures[1].update(status="error", answer=None, sources=None, sources_complete=None)
        report = self.run_sim(fixtures=fixtures)
        self.assertEqual((report["completed"], report["failed"], report["not_started"]), (1, 1, 3))
        failed = report["cases"][1]
        self.assertEqual((failed["stage"], failed["capture_outcome"], failed["output"]["status"]), ("capture", "http_error", "error"))
        self.assertEqual(failed["cleanup"], {"api":"confirmed", "database":"confirmed"})
        self.assertTrue(all(item["output"] is None for item in report["cases"][2:]))

    def test_interruption_reports_pending_and_cleans_resources(self):
        with patch.object(sim, "capture", side_effect=KeyboardInterrupt):
            report = self.run_sim()
        self.assertEqual((report["failed"], report["not_started"]), (1, 4))
        self.assertEqual(report["cases"][0]["error"], "interrupted")
        self.assertEqual(report["cases"][0]["cleanup"], {"api":"confirmed", "database":"confirmed"})

    def test_foreign_source_is_not_accepted(self):
        fixtures = copy.deepcopy(self.fixtures)
        fixtures[1]["sources"][1]["text"] = "texto distinto"
        report = self.run_sim(fixtures=fixtures)
        self.assertEqual((report["completed"], report["failed"], report["not_started"]), (1, 1, 3))
        self.assertEqual(report["cases"][1]["stage"], "source_verify")
        self.assertEqual(report["cases"][1]["error"], "stage_failed")
        self.assertEqual(report["cases"][1]["output"]["status"], "error")

    def test_unconfirmed_cleanup_stops_and_is_not_hidden(self):
        original = sim._close_db
        def close_then_fail(conn):
            original(conn)
            raise RuntimeError("simulated private failure")
        with patch.object(sim, "_close_db", side_effect=close_then_fail):
            report = self.run_sim()
        self.assertEqual((report["completed"], report["failed"], report["not_started"]), (0, 1, 4))
        case = report["cases"][0]
        self.assertEqual((case["error"], case["cleanup"]), ("cleanup_unconfirmed", {"api":"confirmed", "database":"unconfirmed"}))
        self.assertNotIn("private failure", json.dumps(report))

    def test_api_cleanup_failure_still_closes_database(self):
        original = sim._stop_server
        def stop_then_fail(api, thread):
            original(api, thread)
            raise RuntimeError("simulated failure")
        with patch.object(sim, "_stop_server", side_effect=stop_then_fail):
            report = self.run_sim()
        self.assertEqual((report["failed"], report["not_started"]), (1, 4))
        self.assertEqual(report["cases"][0]["cleanup"], {"api":"unconfirmed", "database":"confirmed"})

    def test_invalid_manifest_does_not_create_database(self):
        cases = copy.deepcopy(self.cases)
        cases[1]["attack_source"]["document_id"] = "other"
        self.cases_path.write_bytes(b"".join(sim._encode(row) + b"\n" for row in cases))
        with patch.object(sim, "_open_db", side_effect=AssertionError("must not create")) as opened:
            result = sim.main(["--cases", str(self.cases_path), "--fixtures", str(self.fixtures_path)])
        self.assertEqual(result, 2)
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
