import copy
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
from evals_lab import rag_adapter
from evals_lab.cases import MAX_FILE_BYTES, InputError


def jsonl(records):
    return b"".join((json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
                    for row in records)


class RagAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="evals-rag-adapter-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.assertEqual(self.folder.parent, Path(tempfile.gettempdir()).resolve())
        self.report = self.folder / "private-report.json"
        self.corpus = self.folder / "private-corpus.jsonl"
        self.cases = self.folder / "private-cases.jsonl"
        self.outputs = self.folder / "private-outputs.jsonl"
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")
        for name, path in (("rag_report.json", self.report), ("rag_corpus.jsonl", self.corpus),
                           ("rag_cases.jsonl", self.cases)):
            path.write_bytes((ROOT / "examples" / name).read_bytes())
        self.original_report = self.report.read_bytes()
        self.report_data = json.loads(self.original_report)

    def command(self, *extra):
        return [sys.executable, "-S", "-m", "evals_lab.rag_adapter", "--report", str(self.report),
                "--corpus", str(self.corpus), "--cases", str(self.cases), "--k", "2", *extra]

    def run_adapter(self, *extra):
        return subprocess.run(self.command(*extra), cwd=self.folder, env=self.env, capture_output=True, timeout=20)

    def test_example_is_deterministic_and_feeds_evaluator(self):
        result = self.run_adapter()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b'{"id":"q1","retrieved_document_ids":["A","B","C"]}\n'
                                        b'{"id":"q2","retrieved_document_ids":[]}\n')
        self.assertEqual(result.stdout, self.run_adapter().stdout)
        for private in (b"Pregunta", b"sint", b"Fragmento", b"private-"):
            self.assertNotIn(private, result.stdout + result.stderr)
        self.outputs.write_bytes(result.stdout)
        evaluated = subprocess.run([sys.executable, "-S", "-m", "evals_lab.retrieval", "--corpus", str(self.corpus),
                                    "--cases", str(self.cases), "--outputs", str(self.outputs), "--k", "2"],
                                   cwd=self.folder, env=self.env, capture_output=True, timeout=20)
        self.assertEqual(evaluated.returncode, 1, evaluated.stderr)
        report = json.loads(evaluated.stdout)
        self.assertEqual((report["passed"], report["failed"], report["errors"]), (2, 0, 1))
        self.assertEqual(report["cases"][0]["rank"], 2)
        self.assertEqual(report["positive"]["hit_at_k"], 1)
        self.assertEqual(report["positive"]["mrr_at_k"], .5)
        self.assertEqual(report["positive"]["coverage"], .5)
        self.assertEqual(report["cases"][-1]["error_code"], "missing_response")

    def test_validation_rejects_inconsistent_report_without_output(self):
        def changes():
            for path, value in [(("limit",), True), (("limit",), 21), (("limit",), 1),
                                (("cases", 0, "id"), "NEW"), (("cases", 0, "id"), "unknown"),
                                (("cases", 0, "question"), "PRIVATE_INVALID"),
                                (("cases", 0, "expected_document_id"), "A"),
                                (("cases", 1, "expected_document_id"), None),
                                (("cases", 1, "expected_document_id"), "A"),
                                (("cases", 0, "status"), "answered"),
                                (("cases", 1, "status"), "no_context"),
                                (("cases", 1, "answer"), "   "),
                                (("cases", 1, "sources", 3, "document_id"), "UNKNOWN"),
                                (("cases", 1, "sources", 3, "document_id"), "A B")]:
                data = copy.deepcopy(self.report_data)
                obj = data
                for part in path[:-1]:
                    obj = obj[part]
                obj[path[-1]] = value
                yield str(path), data
            data = copy.deepcopy(self.report_data)
            data["cases"].append(copy.deepcopy(data["cases"][0]))
            yield "duplicated case", data
            data = copy.deepcopy(self.report_data)
            del data["cases"][0]["expected_document_id"]
            yield "missing expected", data
            data = copy.deepcopy(self.report_data)
            data["cases"][1]["sources"].append({"document_id": "A"})
            yield "over limit", data
            data = copy.deepcopy(self.report_data)
            data["cases"][1]["sources"][3] = []
            yield "bad source", data
            yield "not a report", {"error": {"code": "upstream_error"}}
        for label, data in changes():
            with self.subTest(label=label):
                self.report.write_text(json.dumps(data), encoding="utf-8")
                result = self.run_adapter()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, b"")
                for private in (b"PRIVATE_INVALID", b"UNKNOWN", b"private-"):
                    self.assertNotIn(private, result.stderr)

    def test_invalid_json_and_jsonl_are_rejected(self):
        bad_reports = [b"\xef\xbb\xbf" + self.original_report, b"\xff", b"{", b'[]',
                       b'{"limit":4,"limit":4,"cases":[]}',
                       b'{"limit":4,"cases":[],"ignored":{"x":1,"x":2}}',
                       b'{"limit":4,"cases":[],"ignored":NaN}',
                       b'{"limit":4,"cases":[],"ignored":1e999}',
                       b'{"limit":4,"cases":[],"ignored":' + b'9' * 129 + b'}',
                       b"x" * (MAX_FILE_BYTES + 1)]
        for data in bad_reports:
            with self.subTest(data=data[:80]):
                self.report.write_bytes(data)
                result = self.run_adapter()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
        self.report.write_bytes(self.original_report)
        self.corpus.write_bytes(jsonl([{"document_id":"A"}, {"document_id":"A"}]))
        self.assertEqual(self.run_adapter().returncode, 2)
        self.corpus.write_bytes(jsonl([{"document_id":"A"}, {"document_id":"B"}]))
        self.assertEqual(self.run_adapter().returncode, 2)  # Caso ausente también refiere a C.
        self.cases.write_bytes(b'{"id":"q1","input":"x","relevant_document_ids":[]}\n\n')
        self.assertEqual(self.run_adapter().returncode, 2)

    def test_cutoff_boundaries_and_fewer_unique_documents(self):
        for k in ("0", "5", "21", "true", "3.5"):
            result = self.run_adapter("--k", k)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
        data = copy.deepcopy(self.report_data)
        data["cases"][1]["sources"] = [{"document_id":"A"}] * 4
        self.report.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_adapter("--k", "4")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout.splitlines()[0])["retrieved_document_ids"], ["A"])
        self.assertEqual(self.run_adapter("--k", "1").returncode, 0)
        with self.assertRaises(InputError):
            rag_adapter.convert({}, [], [], k=True)

    def test_empty_and_missing_responses_and_stdout_failure(self):
        data = copy.deepcopy(self.report_data)
        data["cases"] = []
        self.report.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_adapter()
        self.assertEqual((result.returncode, result.stdout), (0, b""))
        cases = [{"id":"c" + str(i), "input":"question", "relevant_document_ids":[]} for i in range(1000)]
        data["cases"] = [{"id":case["id"], "question":"question", "expected_document_id":None,
                          "status":"no_context", "answer":"empty", "sources":[]} for case in cases]
        self.report.write_text(json.dumps(data), encoding="utf-8")
        self.cases.write_bytes(jsonl(cases))
        process = subprocess.Popen(self.command(), cwd=self.folder, env=self.env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        process.stdout.close()
        process.stdout = None
        _, stderr = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 2, stderr)
        self.assertNotIn(b"Traceback", stderr)

    def test_unexpected_failure_is_sanitized(self):
        errors = io.StringIO()
        with patch.object(rag_adapter, "_load_report", side_effect=RuntimeError("PRIVATE_FAILURE")), patch.object(sys, "stderr", errors):
            self.assertEqual(rag_adapter.main(["--report", str(self.report), "--corpus", str(self.corpus),
                                               "--cases", str(self.cases)]), 2)
        self.assertNotIn("PRIVATE_FAILURE", errors.getvalue())
        self.assertNotIn("private-", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
