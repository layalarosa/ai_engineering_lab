import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.similarity import cosine_similarity, rank_vectors


class SimilarityTests(unittest.TestCase):
    def test_directions(self):
        self.assertAlmostEqual(cosine_similarity([1, 0], [2, 0]), 1)
        self.assertAlmostEqual(cosine_similarity([1, 0], [0, 2]), 0)
        self.assertAlmostEqual(cosine_similarity([1, 0], [-1, 0]), -1)

    def test_invalid_vectors(self):
        for a, b in [([], []), ([1], [1, 2]), ([0, 0], [1, 0]), ([math.nan], [1]), ([math.inf], [1])]:
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                cosine_similarity(a, b)

    def test_ranking_and_ties(self):
        self.assertEqual(rank_vectors([1, 0], [[0, 1], [1, 0], [2, 0]], 2), [(1, 1.0), (2, 1.0)])
        self.assertEqual(rank_vectors([1], [], 3), [])
        with self.assertRaises(ValueError):
            rank_vectors([0], [])
        with self.assertRaises(ValueError):
            rank_vectors([1], [[1]], 0)


if __name__ == "__main__":
    unittest.main()
