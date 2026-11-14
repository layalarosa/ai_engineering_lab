import http.client
import json
import os
import sys
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.api import Server, Settings, main
from rag.ingestion import DocumentChunk
from rag.search import SearchResult


class APITests(unittest.TestCase):
    def setUp(self):
        try:
            import psycopg  # required by the API; not needed for older standalone tests
        except ImportError:
            self.skipTest("requiere psycopg")
        self.settings = Settings("unused", "w" * 32, "r" * 32, "ollama", "embed", "ollama", "llm", "http://localhost:11434", None)
        self.server = Server(("127.0.0.1", 0), self.settings)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, token, body=b"", media="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": media}
        if token:
            headers["Authorization"] = "Bearer " + token
        conn.request(method, path, body, headers)
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def test_auth_and_input(self):
        self.assertEqual(self.request("POST", "/v1/answers", "", b"{}")[0], 401)
        self.assertEqual(self.request("POST", "/v1/answers", "w" * 32, b"{}")[0], 403)
        self.assertEqual(self.request("PUT", "/v1/documents/..", "w" * 32, b"data", "text/plain")[0], 400)
        self.assertEqual(self.request("PUT", "/v1/documents/doc", "w" * 32, b" ", "text/plain")[0], 422)
        self.assertEqual(self.request("POST", "/v1/answers", "r" * 32, b'{"question":"q","limit":true}')[0], 400)
        self.assertEqual(self.request("PUT", "/v1/documents/doc", "w" * 32, b"x", "application/json")[0], 415)

    def test_index_and_ask_isolate_api_corpus(self):
        db = MagicMock()
        db.__enter__.return_value = db
        result = SearchResult(DocumentChunk("api:doc", 0, "fuente"), .9)
        with patch("psycopg.connect", return_value=db), patch("rag.api.ollama.embed", return_value=[[1.0]]), \
             patch("rag.api.replace_document") as replace, patch("rag.api.search", return_value=[result]) as search, \
             patch("rag.answer.generate", return_value="respuesta"):
            status, result_body = self.request("PUT", "/v1/documents/doc", "w" * 32, b"texto", "text/plain")
            self.assertEqual((status, result_body["chunks_indexed"]), (200, 1))
            self.assertEqual(replace.call_args.args[2], "api:doc")
            status, result_body = self.request("POST", "/v1/answers", "r" * 32, b'{"question":"pregunta"}')
            self.assertEqual((status, result_body["status"], result_body["sources"][0]["document_id"]), (200, "answered", "doc"))
            self.assertEqual(search.call_args.kwargs["source_prefix"], "api:")

    def test_no_context_and_sanitized_error(self):
        db = MagicMock()
        db.__enter__.return_value = db
        with patch("psycopg.connect", return_value=db), patch("rag.api.ollama.embed", return_value=[[1.0]]), \
             patch("rag.api.search", return_value=[]), patch("rag.answer.generate") as generate:
            status, body = self.request("POST", "/v1/answers", "r" * 32, b'{"question":"q"}')
            self.assertEqual((status, body["status"], body["sources"]), (200, "no_context", []))
            generate.assert_not_called()
        with patch("rag.api.ollama.embed", side_effect=RuntimeError("secret-value")):
            status, body = self.request("POST", "/v1/answers", "r" * 32, b'{"question":"q"}')
            self.assertEqual(status, 500)
            self.assertNotIn("secret-value", json.dumps(body))

    @unittest.skipUnless(os.getenv("RAG_TEST_DATABASE_URL"), "requiere RAG_TEST_DATABASE_URL")
    def test_http_with_real_postgres(self):
        import psycopg
        from rag.store import initialize
        dsn = os.environ["RAG_TEST_DATABASE_URL"]
        model = "test-api-" + uuid.uuid4().hex
        self.server.settings = Settings(dsn, "w" * 32, "r" * 32, "ollama", model, "ollama", "llm", "http://localhost:11434", None)
        with psycopg.connect(dsn, autocommit=True) as conn:
            initialize(conn)
        try:
            with patch("rag.api.ollama.embed", return_value=[[1.0, 0.0]]), patch("rag.answer.generate", return_value="respuesta [1]"):
                self.assertEqual(self.request("PUT", "/v1/documents/doc", "w" * 32, b"texto", "text/plain")[0], 200)
                from rag.ollama import OllamaError
                with patch("rag.api.ollama.embed", side_effect=OllamaError("error simulado")):
                    self.assertEqual(self.request("PUT", "/v1/documents/doc", "w" * 32, b"cambio", "text/plain")[0], 502)
                status, body = self.request("POST", "/v1/answers", "r" * 32, b'{"question":"q"}')
                self.assertEqual((status, body["answer"], body["sources"][0]["text"]), (200, "respuesta [1]", "texto"))
        finally:
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute("DELETE FROM rag_chunks WHERE model = %s", (model,))
                conn.execute("DELETE FROM rag_models WHERE model = %s", (model,))


class SettingsTests(unittest.TestCase):
    def test_requires_distinct_tokens(self):
        env = {"DATABASE_URL": "db", "RAG_EMBED_MODEL": "e", "RAG_GENERATION_MODEL": "g", "RAG_WRITE_TOKEN": "a" * 32, "RAG_READ_TOKEN": "a" * 32}
        with patch.dict(os.environ, env, clear=True), self.assertRaises(ValueError):
            Settings.from_env()

    def test_port_configuration(self):
        settings = MagicMock()
        server = MagicMock()
        server.serve_forever.side_effect = KeyboardInterrupt
        with patch.dict(os.environ, {"RAG_PORT": "9010"}), patch("rag.api.Settings.from_env", return_value=settings), \
             patch("rag.api.Server", return_value=server) as factory:
            self.assertEqual(main(), 0)
            factory.assert_called_once_with(("127.0.0.1", 9010), settings)
        with patch.dict(os.environ, {"RAG_PORT": "65536"}), patch("rag.api.Settings.from_env", return_value=settings), \
             patch("rag.api.Server") as factory:
            self.assertEqual(main(), 1)
            factory.assert_not_called()
