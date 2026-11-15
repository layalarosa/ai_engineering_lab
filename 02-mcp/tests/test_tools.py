import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mcp_lab.tools import call_text_stats, text_stats


class ToolTests(unittest.TestCase):
    def test_unicode_empty_and_whitespace(self):
        self.assertEqual(text_stats({"text": ""}), {"characters": 0, "utf8_bytes": 0, "words": 0})
        self.assertEqual(text_stats({"text": "á 😀\t e\u0301"}), {"characters": 7, "utf8_bytes": 12, "words": 3})

    def test_limits_and_invalid_arguments(self):
        self.assertEqual(text_stats({"text": "x" * 4000})["characters"], 4000)
        for args in [{"text": "x" * 4001}, {}, {"text": 2}, {"text": "x", "extra": 1}, {"text": "\ud800"}]:
            with self.subTest(args=args):
                self.assertTrue(call_text_stats(args)["isError"])
