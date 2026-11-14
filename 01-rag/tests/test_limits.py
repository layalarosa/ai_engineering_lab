import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.answer import answer_question
from rag.ingestion import DocumentChunk, ingest_text_file
from rag.ollama import OllamaError, embed, generate
from rag.search import SearchResult


class LimitsTests(unittest.TestCase):
    def test_document_size_and_fragment_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "doc.txt"
            path.write_bytes(b"a" * 1_000_001)
            with self.assertRaisesRegex(ValueError, "bytes"):
                ingest_text_file(path, 500)
            path.write_text("a" * 1002, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fragmentos"):
                ingest_text_file(path, 1)
            path.write_text("a" * 1000, encoding="utf-8")
            self.assertEqual(len(ingest_text_file(path, 1)), 1000)

    def test_question_and_context_bounds(self):
        with self.assertRaisesRegex(ValueError, "pregunta supera"):
            answer_question("a" * 4001, [], model="model")
        result = SearchResult(DocumentChunk("doc", 0, "a" * 20001), 0.9)
        with patch("rag.answer.generate") as mock, self.assertRaisesRegex(ValueError, "contexto supera"):
            answer_question("q", [result], model="model")
        mock.assert_not_called()

    def test_connection_settings_and_response_size(self):
        for url, timeout in [("file:///tmp", 5), ("http://user:pass@host", 5), ("http://localhost", float("nan"))]:
            with self.subTest(url=url, timeout=timeout), self.assertRaises(ValueError):
                embed(["x"], model="model", base_url=url, timeout=timeout)
        with patch("rag.ollama.MAX_RESPONSE_BYTES", 8), patch("rag.ollama.urlopen", return_value=BytesIO(b"x" * 9)):
            with self.assertRaisesRegex(OllamaError, "supera"):
                generate("q", model="model", system="system")
