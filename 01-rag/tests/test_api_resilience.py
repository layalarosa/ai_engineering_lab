"""Pruebas controladas de saturación y fallos de dependencias."""
import http.client
import json
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.api import Server, Settings
from rag.ollama import OllamaError


class APIResilienceTests(unittest.TestCase):
    def setUp(self):
        try:
            import psycopg
        except ImportError:
            self.skipTest("requiere psycopg")
        self.settings = Settings("unused", "w" * 32, "r" * 32, "ollama", "embed", "ollama", "llm", "http://localhost:11434", None)
        self.server = Server(("127.0.0.1", 0), self.settings, max_inflight=2)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def ask(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=8)
        try:
            conn.request("POST", "/v1/answers", b'{"question":"q"}', {"Content-Type": "application/json", "Authorization": "Bearer " + "r" * 32})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_saturation_then_recovery(self):
        entered = threading.Event()
        release = threading.Event()
        lock = threading.Lock()
        count = 0

        def slow_embed(*args, **kwargs):
            nonlocal count
            with lock:
                count += 1
                if count == 2:
                    entered.set()
            if not release.wait(5):
                raise TimeoutError("test timed out")
            return [[1.0]]

        conn = MagicMock()
        conn.__enter__.return_value = conn
        with patch("rag.api.ollama.embed", side_effect=slow_embed), patch("psycopg.connect", return_value=conn), patch("rag.api.search", return_value=[]):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.ask) for _ in range(2)]
                try:
                    self.assertTrue(entered.wait(5), "las dos solicitudes no llegaron al proveedor")
                    try:
                        status, body = self.ask()
                    except (ConnectionError, http.client.RemoteDisconnected):
                        # El SO puede cerrar la conexión al rechazarla antes de leer el cuerpo.
                        pass
                    else:
                        self.assertEqual((status, body["error"]["code"]), (503, "service_unavailable"))
                finally:
                    release.set()
                self.assertEqual([f.result(timeout=8)[0] for f in futures], [200, 200])
            self.assertEqual(self.ask()[0], 200)

    def test_provider_failures_are_sanitized(self):
        failures = [(TimeoutError("secret"), 504, "upstream_timeout"),
                    (URLError("secret"), 503, "service_unavailable"),
                    (HTTPError("http://example", 500, "secret", {}, None), 502, "upstream_error"),
                    (None, 502, "upstream_error")]
        for cause, expected_status, expected_code in failures:
            def fail(*args, **kwargs):
                if cause:
                    raise OllamaError("private-detail") from cause
                raise OllamaError("private-detail")
            with self.subTest(status=expected_status), patch("rag.api.ollama.embed", side_effect=fail):
                status, body = self.ask()
                self.assertEqual((status, body["error"]["code"]), (expected_status, expected_code))
                self.assertNotIn("private-detail", json.dumps(body))
                self.assertNotIn("secret", json.dumps(body))

    def test_database_failure_and_next_request(self):
        import psycopg
        conn = MagicMock()
        conn.__enter__.return_value = conn
        with patch("rag.api.ollama.embed", return_value=[[1.0]]), \
             patch("psycopg.connect", side_effect=[psycopg.OperationalError("secret"), conn]), \
             patch("rag.api.search", return_value=[]):
            status, body = self.ask()
            self.assertEqual((status, body["error"]["code"]), (503, "service_unavailable"))
            self.assertNotIn("secret", json.dumps(body))
            self.assertEqual(self.ask()[0], 200)
