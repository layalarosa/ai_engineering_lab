"""Evaluación opcional con corpus de muestra, API HTTP y servicios reales."""
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
from rag.evaluate_api import evaluate, load_cases
from rag.store import initialize

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("RAG_LIVE_TEST") == "1", "requiere RAG_LIVE_TEST=1 y base de pruebas dedicada")
class LiveEvaluationTests(unittest.TestCase):
    def test_evaluate_real_api(self):
        import psycopg
        dsn = os.environ["RAG_TEST_DATABASE_URL"]
        model = os.environ["RAG_LIVE_EMBED_MODEL"]
        settings = Settings(dsn, secrets.token_urlsafe(32), secrets.token_urlsafe(32), "ollama", model,
                            "ollama", os.environ["RAG_LIVE_GENERATION_MODEL"], os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"), None)
        ids = ("fragmentacion", "embeddings", "persistencia")
        with psycopg.connect(dsn, autocommit=True) as conn:
            initialize(conn)
            exists = conn.execute("SELECT 1 FROM rag_chunks WHERE model = %s AND left(source, 4) = 'api:' LIMIT 1", (model,)).fetchone()
            if exists:
                self.fail("La evaluación requiere un corpus API vacío en la base de pruebas")
        server = Server(("127.0.0.1", 0), settings)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def call(method, path, token, data, content_type):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=180)
            try:
                conn.request(method, path, data, {"Authorization": "Bearer " + token, "Content-Type": content_type})
                response = conn.getresponse()
                body = json.loads(response.read())
                if response.status != 200:
                    raise ValueError(f"API devolvió HTTP {response.status}")
                return body
            finally:
                conn.close()

        try:
            for doc_id in ids:
                text = (ROOT / "eval_data" / (doc_id + ".txt")).read_bytes()
                call("PUT", "/v1/documents/" + doc_id, settings.write_token, text, "text/plain; charset=utf-8")
            def ask(question, limit):
                body = json.dumps({"question": question, "limit": limit}).encode()
                return call("POST", "/v1/answers", settings.read_token, body, "application/json")
            report = evaluate(load_cases(ROOT / "eval_data" / "api_cases.json"), ask, limit=2)
            print("\nInforme de evaluación real:\n" + json.dumps(report, ensure_ascii=False, indent=2))
            self.assertEqual(len(report["cases"]), 4)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            with psycopg.connect(dsn, autocommit=True) as conn:
                for doc_id in ids:
                    conn.execute("DELETE FROM rag_chunks WHERE model = %s AND source = %s", (model, "api:" + doc_id))
