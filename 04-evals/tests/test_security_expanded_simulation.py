import hashlib
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
from evals_lab import security_expanded_simulation as expanded
from evals_lab.security_capture import _error


def line(row):
    return json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def write(path, rows):
    path.write_bytes(b"".join(line(row) + b"\n" for row in rows))


class ExpandedSimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="expanded-sim-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.paths = [self.folder / name for name in ("cases.jsonl", "corpus.jsonl", "expectations.jsonl", "fixtures.jsonl")]
        for path, name in zip(self.paths, ("security_expanded_cases.jsonl", "security_expanded_corpus.jsonl",
                                          "security_control_expectations.jsonl", "security_expanded_simulated_outputs.jsonl")):
            path.write_bytes((ROOT / "examples" / name).read_bytes())
        self.result_dir = self.folder / "run"
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")

    def run_cli(self):
        cmd = [sys.executable, "-S", "-m", "evals_lab.security_expanded_simulation"]
        for key, path in zip(("cases", "corpus", "expectations", "fixtures"), self.paths):
            cmd.extend(("--" + key, str(path)))
        cmd.extend(("--output-dir", str(self.result_dir)))
        return subprocess.run(cmd, cwd=self.folder, env=self.env, capture_output=True, timeout=30)

    def evaluate_cli(self, module, arguments):
        cmd = [sys.executable, "-S", "-m", module]
        for key, path in arguments:
            cmd.extend(("--" + key, str(self.result_dir / path)))
        return subprocess.run(cmd, cwd=self.folder, env=self.env, capture_output=True, timeout=20)

    def test_success_is_ordered_isolated_and_feeds_both_evaluators(self):
        result = self.run_cli()
        self.assertEqual((result.returncode,result.stderr),(0,b""))
        report=json.loads((self.result_dir/"report.json").read_bytes())
        self.assertEqual((report["planned"],report["attempted"],report["completed"],report["failed"],report["not_started"]),(24,24,24,0,0))
        plan=[json.loads(row) for row in (self.result_dir/"plan.jsonl").read_bytes().splitlines()]
        cases=[json.loads(row) for row in (self.result_dir/"cases.jsonl").read_bytes().splitlines()]
        outputs=[json.loads(row) for row in (self.result_dir/"outputs.jsonl").read_bytes().splitlines()]
        expectations=[json.loads(row) for row in (self.result_dir/"expectations.jsonl").read_bytes().splitlines()]
        events=[json.loads(row) for row in (self.result_dir/"events.jsonl").read_bytes().splitlines()]
        self.assertEqual([p["scenario_id"] for p in plan],list(expanded.SCENARIO_IDS)*2)
        self.assertEqual([p["repetition"] for p in plan],[1]*12+[2]*12)
        self.assertEqual(len({p["id"] for p in plan}),24)
        self.assertEqual([p["id"] for p in plan],[c["id"] for c in cases])
        self.assertEqual([p["id"] for p in plan],[o["id"] for o in outputs])
        self.assertEqual((len(expectations),len(events)),(8,48))
        self.assertTrue(all(r["api_cleanup"]=="confirmed" for r in report["attempts"]))
        self.assertEqual(report["output_integrity"],"confirmed")
        self.assertNotIn(b"PENDULO-17",(self.result_dir/"events.jsonl").read_bytes())
        source_lines = [{json.loads(raw)["id"]:hashlib.sha256(raw).hexdigest() for raw in path.read_bytes().splitlines()}
                        for path in self.paths]
        scenario_cases = {row["id"]:row for row in (json.loads(raw) for raw in self.paths[0].read_bytes().splitlines())}
        for item, materialized in zip(plan, cases):
            scenario = item["scenario_id"]
            self.assertEqual({k:v for k,v in materialized.items() if k!="id"},
                             {k:v for k,v in scenario_cases[scenario].items() if k!="id"})
            self.assertEqual(item["scenario_line_sha256"], source_lines[0][scenario])
            self.assertEqual(item["corpus_line_sha256"], source_lines[1][scenario])
            self.assertEqual(item["fixture_line_sha256"], source_lines[3][scenario])
            self.assertEqual(item["expectation_line_sha256"], source_lines[2].get(scenario))
        for first, second in zip(outputs[:12],outputs[12:]):
            self.assertEqual({k:v for k,v in first.items() if k!="id"}, {k:v for k,v in second.items() if k!="id"})
        for filename,digest in report["artifact_sha256"].items():
            self.assertEqual(hashlib.sha256((self.result_dir/filename).read_bytes()).hexdigest(),digest)
        attack=self.evaluate_cli("evals_lab.security",[("cases","cases.jsonl"),("outputs","outputs.jsonl"),("reviews","attack_reviews.jsonl")])
        self.assertEqual(attack.returncode,1,attack.stderr)
        self.assertEqual((json.loads(attack.stdout)["unreviewed"],json.loads(attack.stdout)["followed"]),(16,0))
        controls=self.evaluate_cli("evals_lab.security_controls",[("cases","cases.jsonl"),("expectations","expectations.jsonl"),
                                                                      ("outputs","outputs.jsonl"),("reviews","control_reviews.jsonl")])
        self.assertEqual(controls.returncode,1,controls.stderr)
        control_report=json.loads(controls.stdout)
        self.assertEqual((control_report["control_total"],control_report["unreviewed"],control_report["decided"]),(8,8,0))
        self.assertIsNone(control_report["unjustified_refusal_fraction"])
        self.assertEqual((self.result_dir/"attack_reviews.jsonl").read_bytes(),b"")
        self.assertEqual((self.result_dir/"control_reviews.jsonl").read_bytes(),b"")

    def test_stop_after_capture_failure_and_preserve_partial_artifacts(self):
        cases, corpus, exps, fixtures, ids=expanded.load_inputs(*self.paths)
        attempts, _, _=expanded.build_plan(cases,corpus,exps,fixtures,ids)
        output,event=self.folder/"outputs",self.folder/"events"
        output.write_bytes(b""); event.write_bytes(b"")
        original=expanded.capture
        def capture(case, **kwargs):
            if case["id"]=="f2_c_r1":
                return _error(case["id"]), {"outcome":"transport_error","http_status":None,"request_id":None}
            return original(case,**kwargs)
        with patch.object(expanded,"capture",side_effect=capture) as invoked:
            records,out_ok,event_ok=expanded.execute(attempts,output,event)
        self.assertEqual((invoked.call_count,out_ok,event_ok),(4,"confirmed","confirmed"))
        self.assertEqual([r["status"] for r in records[:5]],["completed","completed","completed","failed","not_started"])
        self.assertTrue(records[3]["capture_started"])
        self.assertEqual(records[3]["api_cleanup"],"confirmed")
        rows=[json.loads(r) for r in output.read_bytes().splitlines()]
        self.assertEqual((len(rows),rows[-1]["id"],rows[-1]["status"]),(4,"f2_c_r1","error"))
        self.assertTrue(all(r["status"]=="not_started" for r in records[4:]))

    def test_pre_request_failure_is_not_counted_as_capture(self):
        with patch.object(expanded,"_start_server",side_effect=OSError("private")):
            report=expanded.run(*self.paths,self.result_dir)
        self.assertEqual((report["status"],report["attempted"],report["failed"],report["not_started"]),("incomplete",0,1,23))
        self.assertEqual(report["attempts"][0]["api_cleanup"],"not_created")
        self.assertNotIn("private",json.dumps(report))
        self.assertEqual(json.loads((self.result_dir/"outputs.jsonl").read_bytes())["status"],"error")

    def test_http_error_fixture_stops_later_cases(self):
        fixtures = [json.loads(x) for x in self.paths[3].read_bytes().splitlines()]
        fixtures[2].update(status="error", answer=None, sources=None, sources_complete=None)
        write(self.paths[3], fixtures)
        report = expanded.run(*self.paths, self.result_dir)
        self.assertEqual((report["completed"],report["failed"],report["attempted"],report["not_started"]),(2,1,3,21))
        self.assertEqual(report["attempts"][2]["capture_outcome"],"http_error")
        output = [json.loads(x) for x in (self.result_dir/"outputs.jsonl").read_bytes().splitlines()]
        self.assertEqual((len(output),output[-1]["status"]),(3,"error"))

    def test_cleanup_failure_stops_without_hiding_it(self):
        original=expanded._stop_server
        def stop_then_fail(server,thread):
            original(server,thread)
            raise RuntimeError("simulated cleanup failure")
        with patch.object(expanded,"_stop_server",side_effect=stop_then_fail):
            report=expanded.run(*self.paths,self.result_dir)
        self.assertEqual((report["attempted"],report["failed"],report["not_started"]),(1,1,23))
        self.assertEqual((report["attempts"][0]["api_cleanup"],report["attempts"][0]["error"]),("unconfirmed","cleanup_unconfirmed"))

    def test_partial_output_write_is_not_retried(self):
        original=expanded._append
        def append(path,data):
            if path.name=="outputs.jsonl":
                with path.open("ab") as stream: stream.write(b"partial")
                raise OSError("partial write")
            return original(path,data)
        with patch.object(expanded,"_append",side_effect=append):
            report=expanded.run(*self.paths,self.result_dir)
        self.assertEqual((report["output_integrity"],report["failed"],report["not_started"]),("unconfirmed",1,23))
        self.assertEqual((self.result_dir/"outputs.jsonl").read_bytes(),b"partial")
        self.assertEqual(report["attempts"][0]["error"],"output_write_unconfirmed")

    def test_event_write_failure_stops_before_capture(self):
        original=expanded._append
        def append(path,data):
            if path.name=="events.jsonl":
                raise OSError("event failure")
            return original(path,data)
        with patch.object(expanded,"_append",side_effect=append), patch.object(expanded,"_start_server") as start:
            report=expanded.run(*self.paths,self.result_dir)
        start.assert_not_called()
        self.assertEqual((report["event_integrity"],report["failed"],report["attempted"],report["not_started"]),
                         ("unconfirmed",1,0,23))
        self.assertEqual((self.result_dir/"outputs.jsonl").read_bytes(),b"")

    def test_invalid_inputs_fail_before_output_or_api(self):
        fixtures=[json.loads(x) for x in self.paths[3].read_bytes().splitlines()]
        fixtures[0]["sources"][0]["text"]="texto ajeno"
        write(self.paths[3],fixtures)
        with patch.object(expanded,"_start_server",side_effect=AssertionError("should not start")) as start:
            with self.assertRaises(expanded.InputError): expanded.run(*self.paths,self.result_dir)
        start.assert_not_called()
        self.assertFalse(self.result_dir.exists())

    def test_existing_output_directory_is_never_overwritten(self):
        self.result_dir.mkdir()
        marker=self.result_dir/"keep.txt"; marker.write_text("keep",encoding="utf-8")
        result=self.run_cli()
        self.assertEqual(result.returncode,2)
        self.assertEqual(marker.read_text(encoding="utf-8"),"keep")
        self.assertEqual(sorted(p.name for p in self.result_dir.iterdir()),["keep.txt"])


if __name__ == "__main__":
    unittest.main()
