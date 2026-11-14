import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.evaluate_api import evaluate, load_cases


class APIEvaluationTests(unittest.TestCase):
    def test_ranking_negatives_and_manual_review_data(self):
        cases = [{"id": "p", "question": "q", "expected_document_id": "good", "expected_answer": "correcta"},
                 {"id": "n", "question": "no", "expected_document_id": None, "expected_answer": "no disponible"}]
        def ask(question, limit):
            if question == "no":
                return {"status": "no_context", "answer": "sin contexto", "sources": []}
            return {"status": "answered", "answer": "respuesta", "sources": [{"document_id": "other"}, {"document_id": "good"}]}
        report = evaluate(cases, ask, 2)
        self.assertEqual((report["hit_at_k"], report["mrr_at_k"]), (1.0, 0.5))
        self.assertEqual((report["negative_cases"], report["negative_no_context"]), (1, 1))
        self.assertEqual(report["cases"][0]["expected_answer"], "correcta")
        self.assertEqual(report["cases"][0]["answer"], "respuesta")

    def test_invalid_api_response_aborts(self):
        case = {"id": "p", "question": "q", "expected_document_id": "doc", "expected_answer": "x"}
        for response in [{}, {"status": "no_context", "answer": "x", "sources": [{"document_id": "doc"}]}]:
            with self.subTest(response=response), self.assertRaises(ValueError):
                evaluate([case], lambda q, k: response)
        with self.assertRaises(ValueError):
            evaluate([case], lambda q, k: {}, limit=0)

    def test_dataset_validation(self):
        case = {"id": "one", "question": "q", "expected_document_id": "../doc", "expected_answer": "x"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps([case]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_cases(path)
            case["expected_document_id"] = None
            path.write_text(json.dumps([case]), encoding="utf-8")
            self.assertEqual(load_cases(path), [case])
