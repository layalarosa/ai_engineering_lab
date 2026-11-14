import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.calibrate import analyze


class CalibrationTests(unittest.TestCase):
    def test_threshold_tradeoffs(self):
        cases = [{"expected_document_id": "a"}, {"expected_document_id": "b"}, {"expected_document_id": None}]
        rankings = [[("a", 0.7)], [("b", 0.5)], [("a", 0.6)]]
        low, high = analyze(cases, rankings, [0.5, 0.65])
        self.assertEqual((low["positive_hit"], low["negative_no_context"]), (2, 0))
        self.assertEqual((high["positive_hit"], high["positive_no_context"], high["negative_no_context"]), (1, 1, 1))

    def test_reject_invalid_inputs(self):
        with self.assertRaises(ValueError):
            analyze([{"expected_document_id": None}], [])
        with self.assertRaises(ValueError):
            analyze([], [], [math.nan])
