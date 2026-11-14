"""Prueba opcional de extremo a extremo con PostgreSQL y Ollama reales."""
import http.client
import json
import os
import secrets
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.api import Server, Settings
from rag.store import initialize


@unittest.skipUnless(os.environ.get("RAG_LIVE_TEST") == "1", "prueba real opcional; requiere RAG_LIVE_TEST=1")
class LiveAPITests(unittest.TestCase):
    def test_index_and_answer_using_real_services(self):
        import psycopg
        dsn = os.environ["RAG_TEST_DATABASE_URL"]
        model = os.environ["RAG_LIVE_EMBED_MODEL"]
        generation_model = os.environ["RAG_LIVE_GENERATION_MODEL"]
        url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        write_token, read_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        doc_id = "smoke_" + secrets.token_hex(12)
        settings = Settings(dsn, write_token, read_token, "ollama", model, "ollama", generation_model, url, None)
        with psycopg.connect(dsn, autocommit=True) as conn:
            initialize(conn)
        server = Server(("127.0.0.1", 0), settings)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(method, path, token, data, content_type):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=180)
            try:
                conn.request(method, path, body=data, headers={"Authorization": "Bearer " + token, "Content-Type": content_type})
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()

        try:
            status, document = request("PUT", "/v1/documents/" + doc_id, write_token,
                                       "El proyecto Faro usa el color violeta para identificar sus informes.".encode("utf-8"), "text/plain; charset=utf-8")
            self.assertEqual(status, 200, document)
            self.assertEqual(document["document_id"], doc_id)
            status, answer = request("POST", "/v1/answers", read_token,
                                     json.dumps({"question": "¿Qué color identifica los informes del proyecto Faro?", "limit": 3}).encode("utf-8"), "application/json")
            self.assertEqual(status, 200, answer)
            self.assertEqual(answer["status"], "answered")
            self.assertTrue(answer["answer"].strip())
            self.assertTrue(any(s["document_id"] == doc_id for s in answer["sources"]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute("DELETE FROM rag_chunks WHERE model = %s AND source = %s", (settings.model_key, "api:" + doc_id))
