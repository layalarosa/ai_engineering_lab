import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.evaluate import evaluate, load_cases
from rag.ingestion import DocumentChunk
from rag.search import SearchResult


class EvaluationTests(unittest.TestCase):
    def test_metrics_and_negative_case(self):
        cases = [
            {"id": "a", "question": "q1", "expected_source": "a", "expected_answer": "x"},
            {"id": "b", "question": "q2", "expected_source": "b", "expected_answer": "y"},
            {"id": "n", "question": "q3", "expected_source": None, "expected_answer": "no"},
        ]
        results = [SearchResult(DocumentChunk("other", 0, "z"), 0.9),
                   SearchResult(DocumentChunk("a", 0, "x"), 0.7)]
        report = evaluate(cases, lambda q, k: results[:k], limit=2,
                          generate_answer=lambda q, r: "respuesta")
        self.assertEqual(report["hit_at_k"], 0.5)
        self.assertEqual(report["mrr_at_k"], 0.25)
        self.assertEqual(report["cases"][0]["rank"], 2)
        self.assertEqual(report["negative_cases"], 1)
        self.assertIsNone(report["cases"][2]["rank"])
        self.assertEqual(report["cases"][0]["answer"], "respuesta")
        filtered = evaluate(cases[:1], lambda q, k: results, limit=2, min_score=0.8)
        self.assertEqual(filtered["hit_at_k"], 0)

    def test_load_validates_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            case = {"id": "one", "question": "q", "expected_source": None, "expected_answer": "x"}
            path.write_text(json.dumps([case]), encoding="utf-8")
            self.assertEqual(load_cases(path), [case])
            for invalid in [[], [case, case], [{"id": "bad"}]]:
                path.write_text(json.dumps(invalid), encoding="utf-8")
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    load_cases(path)

    def test_invalid_parameters_and_no_positives(self):
        case = {"id": "n", "question": "q", "expected_source": None, "expected_answer": "no"}
        self.assertIsNone(evaluate([case], lambda q, k: [], limit=1)["hit_at_k"])
        for limit, score in [(0, None), (1, float("nan")), (1, 2)]:
            with self.subTest(limit=limit, score=score), self.assertRaises(ValueError):
                evaluate([case], lambda q, k: [], limit=limit, min_score=score)
