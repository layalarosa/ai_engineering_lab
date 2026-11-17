import copy
import hashlib
import io
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
from evals_lab import rag_grounding_adapter
from evals_lab.cases import MAX_FILE_BYTES


def jsonl(records):
    return b"".join((json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                    for item in records)


class RagGroundingAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="evals-rag-grounding-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.assertEqual(self.folder.parent, Path(tempfile.gettempdir()).resolve())
        self.report = self.folder / "private-report.json"
        self.cases = self.folder / "private-cases.jsonl"
        self.outputs = self.folder / "private-outputs.jsonl"
        self.reviews = self.folder / "private-reviews.jsonl"
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")
        self.report.write_bytes((ROOT / "examples" / "rag_report.json").read_bytes())
        self.cases.write_bytes((ROOT / "examples" / "rag_grounding_cases.jsonl").read_bytes())
        self.data = json.loads(self.report.read_bytes())

    def command(self):
        return [sys.executable, "-S", "-m", "evals_lab.rag_grounding_adapter",
                "--report", str(self.report), "--cases", str(self.cases)]

    def run_adapter(self):
        return subprocess.run(self.command(), cwd=self.folder, env=self.env, capture_output=True, timeout=30)

    def write_report(self, data):
        self.report.write_bytes(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def evaluate(self):
        return subprocess.run([sys.executable, "-S", "-m", "evals_lab.grounding", "--cases", str(self.cases),
                               "--outputs", str(self.outputs), "--reviews", str(self.reviews)],
                              cwd=self.folder, env=self.env, capture_output=True, timeout=20)

    def test_sources_order_missing_case_and_unreviewed_pipeline(self):
        result = self.run_adapter()
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        self.assertEqual(result.stdout, self.run_adapter().stdout)
        rows = [json.loads(row) for row in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["q1", "q2"])
        self.assertEqual([s["source_id"] for s in rows[0]["sources"]], ["s1", "s2", "s3", "s4"])
        self.assertEqual([s["document_id"] for s in rows[0]["sources"]], ["A", "A", "B", "C"])
        self.assertEqual(rows[0]["answer"], self.data["cases"][1]["answer"])
        self.assertEqual([s["text"] for s in rows[0]["sources"]], [s["text"] for s in self.data["cases"][1]["sources"]])
        self.assertEqual(rows[1]["sources"], [])
        self.outputs.write_bytes(result.stdout)
        self.reviews.write_bytes(b"")
        evaluated = self.evaluate()
        self.assertEqual(evaluated.returncode, 1, evaluated.stderr)
        report = json.loads(evaluated.stdout)
        self.assertEqual((report["review_missing"], report["missing_output"]), (2, 1))
        self.assertIsNone(report["supported_fraction"])

    def test_exact_example_unicode_and_hash(self):
        self.cases.write_bytes(jsonl([{"id":"q1", "input":"Que ocurre?"}]))
        data = {"limit":2, "cases":[{"id":"q1", "question":"Que ocurre?", "status":"answered",
                    "answer":"Se preserva el contexto.", "sources":[
                        {"label":"[1]", "document_id":"docA", "chunk_index":0, "text":"Parte A.\n"},
                        {"label":"[2]", "document_id":"docA", "chunk_index":1, "text":"Parte B."}]}]}
        self.write_report(data)
        result = self.run_adapter()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(hashlib.sha256(result.stdout[:-1]).hexdigest(),
                         "ecefd59e30192c731cb6669492b250e13c4f46aa9ce3b98cb7d70d32d9020a03")
        data["cases"][0]["answer"] = "😀 á e\u0301\n"
        data["cases"][0]["sources"][0]["text"] = "😀\r\ná e\u0301"
        self.write_report(data)
        converted = json.loads(self.run_adapter().stdout)
        self.assertEqual(converted["answer"], "😀 á e\u0301\n")
        self.assertEqual(converted["sources"][0]["text"], "😀\r\ná e\u0301")

    def test_review_binds_exact_converted_line(self):
        result = self.run_adapter()
        first_line = result.stdout.splitlines()[0]
        self.outputs.write_bytes(result.stdout)
        case_line = self.cases.read_bytes().splitlines()[0]
        self.reviews.write_bytes(jsonl([{"id":"q1", "schema_version":2,
             "case_sha256":hashlib.sha256(case_line).hexdigest(), "response_sha256":hashlib.sha256(first_line).hexdigest(),
             "review_status":"complete", "abstention":False, "claims":[{"claim_id":"c1", "claim":"ejemplo",
             "answer_span":{"start":0,"end":1}, "evidence":[{"source_id":"s1","start":0,"end":1,"relation":"supports"}]}]}]))
        report = json.loads(self.evaluate().stdout)
        self.assertEqual((report["supported"], report["review_missing"], report["missing_output"]), (1, 1, 1))
        data = copy.deepcopy(self.data)
        data["cases"][1]["sources"][0]["text"] += " cambio"
        self.write_report(data)
        self.outputs.write_bytes(self.run_adapter().stdout)
        stale = self.evaluate()
        self.assertEqual((stale.returncode, stale.stdout), (2, b""))

    def test_bad_records_fail_without_partial_output(self):
        def changes():
            for path, value in [(("limit",), True), (("limit",), 0), (("limit",), 3),
                                (("cases", 1, "id"), "UNKNOWN"), (("cases", 1, "question"), "PRIVATE_CONTENT"),
                                (("cases", 0, "status"), "answered"), (("cases", 1, "status"), "no_context"),
                                (("cases", 1, "answer"), "  "), (("cases", 1, "answer"), "x" * 4001),
                                (("cases", 1, "sources", 3, "label"), "[1]"),
                                (("cases", 1, "sources", 3, "chunk_index"), True),
                                (("cases", 1, "sources", 3, "chunk_index"), 1000),
                                (("cases", 1, "sources", 3, "text"), "x" * 4001),
                                (("cases", 1, "sources", 3, "document_id"), "PRIVATE INVALID")]:
                data = copy.deepcopy(self.data); obj = data
                for key in path[:-1]: obj = obj[key]
                obj[path[-1]] = value
                yield str(path), data
            data = copy.deepcopy(self.data); del data["cases"][1]["sources"][1]["text"]
            yield "missing text", data
            data = copy.deepcopy(self.data); data["cases"].append(copy.deepcopy(data["cases"][0]))
            yield "duplicate ID", data
            data = copy.deepcopy(self.data); data["cases"][1]["sources"][2] = None
            yield "non-object source", data
            yield "error response", {"error":{"code":"server_error"}}
        for name, data in changes():
            with self.subTest(name=name):
                self.write_report(data)
                result = self.run_adapter()
                self.assertEqual((result.returncode, result.stdout), (2, b""), result.stderr)
                for private in (b"PRIVATE_CONTENT", b"PRIVATE INVALID", b"private-"):
                    self.assertNotIn(private, result.stderr)

    def test_invalid_json_and_empty_report(self):
        for raw in (b'\xef\xbb\xbf{}', b'\xff', b'[]', b'{', b'{"limit":2,"limit":2,"cases":[]}',
                    b'{"limit":1,"cases":[{"id":"q1","question":"Pregunta positiva","status":"no_context","answer":"\\ud800","sources":[]}]}',
                    b'{"limit":2,"cases":[],"ignored":{"x":1,"x":2}}',
                    b'{"limit":2,"cases":[],"ignored":NaN}', b'{"limit":2,"cases":[],"ignored":1e999}',
                    b'{"limit":2,"cases":[],"ignored":' + b'9' * 129 + b'}', b'x' * (MAX_FILE_BYTES + 1)):
            with self.subTest(data=raw[:80]):
                self.report.write_bytes(raw)
                result = self.run_adapter()
                self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.write_report({"limit":2, "cases":[]})
        self.assertEqual((self.run_adapter().returncode, self.run_adapter().stdout), (0, b""))
        self.cases.write_bytes(b'{"id":"q1","input":"x"}\n\n')
        self.assertEqual(self.run_adapter().returncode, 2)

    def test_output_limits_after_unicode_escaping(self):
        def record(index, length):
            return {"id":"q" + str(index), "question":"x", "status":"answered", "answer":"ok",
                    "sources":[{"label":"[" + str(i) + "]", "document_id":"D", "chunk_index":i,
                                "text":"😀" * length} for i in range(1, 21)]}
        self.cases.write_bytes(jsonl([{"id":"q0", "input":"x"}]))
        self.write_report({"limit":20, "cases":[record(0, 4000)]})
        result = self.run_adapter()
        self.assertEqual((result.returncode, result.stdout), (2, b""))  # Una línea supera 65.536 bytes.
        self.cases.write_bytes(jsonl([{"id":"q" + str(i), "input":"x"} for i in range(45)]))
        self.write_report({"limit":20, "cases":[record(i, 200) for i in range(45)]})
        self.assertLess(self.report.stat().st_size, MAX_FILE_BYTES)
        result = self.run_adapter()
        self.assertEqual((result.returncode, result.stdout), (2, b""))  # Total supera 2 MiB.

    def test_unexpected_failure_and_closed_stdout(self):
        errors = io.StringIO()
        with patch.object(rag_grounding_adapter, "_load_report", side_effect=RuntimeError("PRIVATE_FAILURE")), patch.object(sys, "stderr", errors):
            self.assertEqual(rag_grounding_adapter.main(["--report", str(self.report), "--cases", str(self.cases)]), 2)
        self.assertNotIn("PRIVATE_FAILURE", errors.getvalue())
        cases = [{"id":"c" + str(i), "input":"x"} for i in range(1000)]
        self.cases.write_bytes(jsonl(cases))
        self.write_report({"limit":1,"cases":[{"id":c["id"], "question":"x", "status":"no_context",
                                               "answer":"sin contexto", "sources":[]} for c in cases]})
        process = subprocess.Popen(self.command(), cwd=self.folder, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.stdout.close()
        process.stdout = None
        _, stderr = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 2, stderr)
        self.assertNotIn(b"Traceback", stderr)


if __name__ == "__main__":
    unittest.main()
