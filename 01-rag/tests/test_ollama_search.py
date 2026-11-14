import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.ollama import OllamaError, embed
from rag.search import search_file


class OllamaTests(unittest.TestCase):
    def test_http_request_and_search_pipeline(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, body))
                result = json.dumps({"embeddings": [[1, 0], [0, 1], [0, 1]]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(result)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "doc.txt"
                path.write_text("abcdefgh", encoding="utf-8")
                results = search_file(path, "pregunta", model="test", base_url=f"http://127.0.0.1:{server.server_port}", chunk_size=4, overlap=0)
            self.assertEqual(results[0].chunk.text, "efgh")
            self.assertEqual(results[0].score, 1)
            self.assertEqual(requests, [("/api/embed", {"model": "test", "input": ["abcd", "efgh", "pregunta"], "truncate": False})])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_invalid_response_and_connection_failure(self):
        from io import BytesIO
        for data in [b"bad json", b'{}', b'{"embeddings": [[0, 0]]}', b'{"embeddings": [[1], [2]]}']:
            with self.subTest(data=data), patch("rag.ollama.urlopen", return_value=BytesIO(data)):
                with self.assertRaises(OllamaError):
                    embed(["text"], model="test")
        with patch("rag.ollama.urlopen", side_effect=OSError("offline")):
            with self.assertRaises(OllamaError):
                embed(["text"], model="test")

    def test_empty_document_does_not_call_model(self):
        with tempfile.TemporaryDirectory() as directory, patch("rag.search.embed") as mock:
            path = Path(directory) / "empty.txt"
            path.write_text("", encoding="utf-8")
            self.assertEqual(search_file(path, "query", model="test"), [])
            mock.assert_not_called()
