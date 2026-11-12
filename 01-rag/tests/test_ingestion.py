import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.ingestion import DocumentChunk, ingest_text_file


class IngestionTests(unittest.TestCase):
    def test_ingests_utf8_and_preserves_source_and_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "documento.txt"
            path.write_text("ábcdef", encoding="utf-8")
            self.assertEqual(ingest_text_file(path, 4, 1), [
                DocumentChunk(str(path), 0, "ábcd"),
                DocumentChunk(str(path), 1, "def"),
            ])

    def test_empty_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.txt"
            path.write_text("", encoding="utf-8")
            self.assertEqual(ingest_text_file(path, 4), [])

    def test_missing_and_invalid_utf8_files_raise_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "document.txt"
            with self.assertRaises(FileNotFoundError):
                ingest_text_file(path, 4)
            path.write_bytes(b"\xff")
            with self.assertRaises(UnicodeDecodeError):
                ingest_text_file(path, 4)


if __name__ == "__main__":
    unittest.main()
