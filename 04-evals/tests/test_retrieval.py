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
from evals_lab import retrieval
from evals_lab.cases import InputError, MAX_FILE_BYTES, MAX_LINE_BYTES


def rows(records):
    return b"\n".join(json.dumps(item, ensure_ascii=True, separators=(",", ":")).encode("ascii")
                      for item in records) + (b"\n" if records else b"")


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="evals-retrieval-test-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.assertEqual(self.folder.parent, Path(tempfile.gettempdir()).resolve())
        self.corpus = self.folder / "private-corpus.jsonl"
        self.cases = self.folder / "private-cases.jsonl"
        self.outputs = self.folder / "private-outputs.jsonl"
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")
        for name, path in (("retrieval_corpus.jsonl", self.corpus), ("retrieval_cases.jsonl", self.cases),
                           ("retrieval_outputs.jsonl", self.outputs)):
            path.write_bytes((ROOT / "examples" / name).read_bytes())

    def command(self, *extra):
        return [sys.executable, "-S", "-m", "evals_lab.retrieval", "--corpus", str(self.corpus),
                "--cases", str(self.cases), "--outputs", str(self.outputs), *extra]

    def run_cli(self, *extra):
        return subprocess.run(self.command(*extra), cwd=self.folder, env=self.env, capture_output=True, timeout=20)

    def test_example_report_and_determinism(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, b"")
        report = json.loads(result.stdout)
        self.assertEqual((report["total"], report["passed"], report["failed"], report["errors"]), (5, 2, 2, 1))
        self.assertEqual(report["positive"], {"total": 3, "evaluated": 2, "missing": 1, "coverage": 2/3,
                                              "hit_at_k": .5, "mrr_at_k": .25})
        self.assertEqual(report["negative"], {"total": 2, "evaluated": 2, "missing": 0, "coverage": 1,
                                              "empty_returned": 1, "nonempty_returned": 1,
                                              "negative_nonempty_rate": .5})
        self.assertEqual([(r["id"], r["status"], r["rank"]) for r in report["cases"]],
                         [("q1", "passed", 2), ("q2", "failed", None), ("q3", "passed", None),
                          ("q4", "failed", None), ("q_missing", "error", None)])
        self.assertEqual(report["cases"][-1]["error_code"], "missing_response")
        for key, path in (("corpus_sha256", self.corpus), ("dataset_sha256", self.cases),
                          ("outputs_sha256", self.outputs)):
            self.assertEqual(report[key], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(result.stdout, (json.dumps(report, sort_keys=True, ensure_ascii=True,
                                                    separators=(",", ":")) + "\n").encode("ascii"))
        self.assertEqual(result.stdout, self.run_cli().stdout)
        for private in (b"Pregunta", b"DOC_", b"private-"):
            self.assertNotIn(private, result.stdout + result.stderr)

    def test_ranks_and_cutoff(self):
        docs = ["D1", "D2", "D3", "D4"]
        self.corpus.write_bytes(rows([{"document_id": d} for d in docs]))
        self.cases.write_bytes(rows([{"id": "c" + str(i), "input": "á😀", "relevant_document_ids": [d]}
                                     for i, d in enumerate(docs)]))
        self.outputs.write_bytes(rows([{"id": "c" + str(i), "retrieved_document_ids": docs}
                                       for i in reversed(range(4))]))
        report = json.loads(self.run_cli("--k", "3").stdout)
        self.assertEqual([c["rank"] for c in report["cases"]], [1, 2, 3, None])
        self.assertEqual(report["positive"]["hit_at_k"], .75)
        self.assertAlmostEqual(report["positive"]["mrr_at_k"], (1 + .5 + 1/3)/4)
        self.assertEqual(self.run_cli("--k", "20").returncode, 0)
        self.assertEqual(self.run_cli("--k", "1").returncode, 1)
        self.assertIsNone(report["negative"]["coverage"])
        self.assertIsNone(report["negative"]["negative_nonempty_rate"])

    def test_empty_and_missing_are_distinct_and_null_denominators(self):
        self.cases.write_bytes(rows([{"id": "negative", "input": "", "relevant_document_ids": []},
                                     {"id": "missing", "input": "", "relevant_document_ids": []}]))
        self.outputs.write_bytes(rows([{"id": "negative", "retrieved_document_ids": []}]))
        report = json.loads(self.run_cli().stdout)
        self.assertEqual((report["passed"], report["errors"]), (1, 1))
        self.assertEqual(report["negative"]["coverage"], .5)
        self.assertEqual(report["negative"]["negative_nonempty_rate"], 0)
        self.assertIsNone(report["positive"]["hit_at_k"])
        self.assertIsNone(report["positive"]["coverage"])
        self.outputs.write_bytes(b"")
        report = json.loads(self.run_cli().stdout)
        self.assertIsNone(report["negative"]["negative_nonempty_rate"])
        self.assertEqual(report["negative"]["coverage"], 0)

    def test_invalid_data_is_rejected_before_report(self):
        valid_corpus, valid_cases, valid_outputs = (p.read_bytes() for p in (self.corpus, self.cases, self.outputs))
        mutations = [
            (self.corpus, b""), (self.corpus, b'\xef\xbb\xbf' + valid_corpus),
            (self.corpus, valid_corpus + b'{"document_id":"DOC_A"}\n'),
            (self.corpus, rows([{"document_id": "bad id"}])),
            (self.cases, b'{"id":"x","id":"y","input":"","relevant_document_ids":[]}\n'),
            (self.cases, rows([{"id":"x", "input":"", "relevant_document_ids":["DOC_A", "DOC_A"]}])),
            (self.cases, rows([{"id":"x", "input":"", "relevant_document_ids":["doc_a"]}])),
            (self.cases, rows([{"id":"x", "input":"", "relevant_document_ids":None}])),
            (self.cases, rows([{"id":"x", "input":"\\ud800", "relevant_document_ids":[]}])),
            (self.outputs, rows([{"id":"q1", "retrieved_document_ids":["DOC_A", "DOC_B", "DOC_C", "DOC_A"]}])),
            (self.outputs, rows([{"id":"q1", "retrieved_document_ids":["DOC_A", "DOC_B", "DOC_C", "unknown"]}])),
            (self.outputs, rows([{"id":"extra", "retrieved_document_ids":[]}])),
            (self.outputs, valid_outputs + b'\n'),
            (self.outputs, b'{"id":"q1","retrieved_document_ids":[1]}\n'),
            (self.outputs, b'{"id":"q1","retrieved_document_ids":[],"extra":"x"}\n'),
        ]
        for path, data in mutations:
            with self.subTest(data=data[:120]):
                for target, content in zip((self.corpus, self.cases, self.outputs),
                                           (valid_corpus, valid_cases, valid_outputs)):
                    target.write_bytes(content)
                path.write_bytes(data)
                result = self.run_cli()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertNotIn(b"private-", result.stderr)
                self.assertNotIn(b"unknown", result.stderr)

    def test_limits_and_k_validation(self):
        docs = ["D" + str(i) for i in range(21)]
        self.corpus.write_bytes(rows([{"document_id": d} for d in docs]))
        self.cases.write_bytes(rows([{"id":"x", "input":"x" * 4000, "relevant_document_ids":docs[:20]}]))
        self.outputs.write_bytes(rows([{"id":"x", "retrieved_document_ids":docs[:20]}]))
        self.assertEqual(self.run_cli("--k", "20").returncode, 0)
        for k in ("0", "21", "true", "3.0"):
            self.assertEqual(self.run_cli("--k", k).returncode, 2)
        for k in (True, 0, 21):
            with self.assertRaises(InputError):
                retrieval.evaluate([], [], k=k, corpus_sha256="", dataset_sha256="", outputs_sha256="")
        self.outputs.write_bytes(rows([{"id":"x", "retrieved_document_ids":docs}]))
        self.assertEqual(self.run_cli().returncode, 2)
        self.outputs.write_bytes(rows([{"id":"x", "retrieved_document_ids":[]}]))
        self.cases.write_bytes(rows([{"id":"x", "input":"x" * 4001, "relevant_document_ids":[]}]))
        self.assertEqual(self.run_cli().returncode, 2)
        self.corpus.write_bytes(b"x" * (MAX_LINE_BYTES + 1))
        self.assertEqual(self.run_cli().returncode, 2)
        self.corpus.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        self.assertEqual(self.run_cli().returncode, 2)
        self.corpus.write_bytes(rows([{"document_id":"D" + str(i)} for i in range(1001)]))
        self.assertEqual(self.run_cli().returncode, 2)

    def test_unexpected_errors_and_closed_stdout(self):
        errors = io.StringIO()
        with patch.object(retrieval, "load_inputs", side_effect=RuntimeError("PRIVATE_FAILURE")), patch.object(sys, "stderr", errors):
            self.assertEqual(retrieval.main(["--corpus", str(self.corpus), "--cases", str(self.cases),
                                             "--outputs", str(self.outputs)]), 2)
        self.assertNotIn("PRIVATE_FAILURE", errors.getvalue())
        self.cases.write_bytes(rows([{"id": "c" + str(i), "input":"", "relevant_document_ids":[]}
                                     for i in range(1000)]))
        self.outputs.write_bytes(b"")
        process = subprocess.Popen(self.command(), cwd=self.folder, env=self.env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        process.stdout.close()
        process.stdout = None
        _, stderr = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 2, stderr)
        self.assertNotIn(b"Traceback", stderr)


if __name__ == "__main__":
    unittest.main()
