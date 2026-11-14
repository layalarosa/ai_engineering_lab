import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "proxy_fixture"))
from load_probe import quantile, summarize


class LoadSummaryTests(unittest.TestCase):
    def test_small_sample_and_rejections(self):
        summary = summarize([{"status": 200, "elapsed_ms": 100}, {"status": 503, "elapsed_ms": 20}], 2)
        self.assertEqual(summary["status_counts"], {"200": 1, "503": 1})
        self.assertEqual(summary["successful_latency_ms"]["p50"], 100)
        self.assertIsNone(summary["successful_latency_ms"]["p95"])
        self.assertIsNone(summary["successful_latency_ms"]["p99"])
        self.assertEqual(summary["completed_per_second_over_burst"], .5)

    def test_percentiles_use_nearest_rank(self):
        self.assertEqual(quantile(list(range(1, 101)), .95), 95)
        self.assertEqual(quantile(list(range(1, 101)), .99), 99)
        self.assertIsNone(quantile([], .5))
