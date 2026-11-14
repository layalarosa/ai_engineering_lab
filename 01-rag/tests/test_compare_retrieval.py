import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.compare_retrieval import rankings, summarize, tokens


class RetrievalComparisonTests(unittest.TestCase):
    def test_normalizes_accents_and_reorders_when_words_help(self):
        self.assertIn("vector", tokens("VÉCTOR y vector"))
        docs = [("a", "texto ajeno"), ("b", "consulta vectorial")]
        results = rankings("consulta vectorial", [1, 0], docs, [[1, 0], [0.99, 0.1]])
        self.assertEqual(results["semantic"][0]["document_id"], "a")
        self.assertEqual(results["hybrid"][0]["document_id"], "b")

    def test_metrics_exclude_negative_cases(self):
        cases = [{"expected_document_id": "a"}, {"expected_document_id": "b"}, {"expected_document_id": None}]
        first = {"semantic": [{"document_id": "a"}, {"document_id": "b"}],
                 "hybrid": [{"document_id": "b"}, {"document_id": "a"}]}
        summary = summarize(cases, [first, first, first])
        self.assertEqual(summary["semantic"]["hit_at_1"], 0.5)
        self.assertEqual(summary["semantic"]["mrr"], 0.75)
        self.assertEqual(summary["negative_cases"], 1)
        self.assertEqual(summary["hybrid"]["hit_at_2"], 1.0)

    def test_invalid_vectors_and_empty_terms(self):
        self.assertEqual(tokens("de la y"), set())
        with self.assertRaises(ValueError):
            rankings("q", [1], [("x", "x")], [])
        with self.assertRaises(ValueError):
            summarize([{"expected_document_id": "x"}], [])
