import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.chunking import chunk_text


class ChunkTextTests(unittest.TestCase):
    def test_overlap_preserves_boundary_context(self):
        self.assertEqual(chunk_text("abcdefghij", 4, 1), ["abcd", "defg", "ghij"])

    def test_empty_and_short_text(self):
        self.assertEqual(chunk_text("", 4), [])
        self.assertEqual(chunk_text("abc", 4), ["abc"])

    def test_exact_boundary_has_no_extra_chunk(self):
        self.assertEqual(chunk_text("abcdef", 3), ["abc", "def"])

    def test_invalid_parameters(self):
        for size, overlap in [(0, 0), (-1, 0), (3, -1), (3, 3), (3, 4)]:
            with self.subTest(size=size, overlap=overlap):
                with self.assertRaises(ValueError):
                    chunk_text("abc", size, overlap)


if __name__ == "__main__":
    unittest.main()
