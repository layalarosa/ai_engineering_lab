import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mcp_lab.config import rag_config
from mcp_lab.protocol import MAX_TOOL_CALLS, Session
from mcp_lab.rag_client import MAX_RESPONSE_BYTES, call_ask_rag


ANSWER = {"status": "answered", "answer": "Respuesta [1]", "sources": [
    {"label": "[1]", "document_id": "guia", "chunk_index": 0, "score": 0.82, "text": "Contexto"}
]}


@contextmanager
def api(status=200, payload=None, content_type="application/json", delay=0, headers=None):
    state = {"requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["requests"].append((self.path, self.headers.get("Authorization"), self.rfile.read(int(self.headers["Content-Length"]))))
            if delay:
                time.sleep(delay)
            data = payload if payload is not None else json.dumps(ANSWER).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)
            except ConnectionError:
                pass

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        with patch.dict(os.environ, {"MCP_RAG_BASE_URL": f"http://127.0.0.1:{server.server_port}", "MCP_RAG_READ_TOKEN": "secret-test"}):
            os.environ.pop("MCP_RAG_READ_TOKEN_FILE", None)
            yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class RagTests(unittest.TestCase):
    def test_success_and_exact_request(self):
        with api() as state:
            result = call_ask_rag({"question": "¿Qué es?"})
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), ANSWER)
        self.assertEqual(len(state["requests"]), 1)
        path, authorization, body = state["requests"][0]
        self.assertEqual((path, authorization), ("/v1/answers", "Bearer secret-test"))
        self.assertEqual(json.loads(body), {"question": "¿Qué es?", "limit": 3})

    def test_demo_calls_rag_and_does_not_expose_token(self):
        with api() as state:
            completed = subprocess.run([sys.executable, str(ROOT / "demo.py"), "--ask", "Pregunta", "--limit", "2"],
                                       capture_output=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(b"RAG:", completed.stdout)
        self.assertNotIn(b"secret-test", completed.stdout + completed.stderr)
        self.assertEqual(json.loads(state["requests"][0][2]), {"question": "Pregunta", "limit": 2})

    def test_no_context_and_custom_limit(self):
        expected = {"status": "no_context", "answer": "No hay contexto", "sources": []}
        with api(payload=json.dumps(expected).encode()) as state:
            result = call_ask_rag({"question": "hola", "limit": 20})
        self.assertEqual(json.loads(result["content"][0]["text"]), expected)
        self.assertEqual(json.loads(state["requests"][0][2])["limit"], 20)

    def test_maximum_unicode_questions_fit_rag_http_limit_without_losing_text(self):
        # Límite de cuerpo documentado de POST /v1/answers en 01-rag.
        questions = ["😀" * 4000, "\x00" * 4000, ("😀\x00á\\" * 1000)]
        with api() as state:
            for question in questions:
                with self.subTest(sample=repr(question[:5])):
                    result = call_ask_rag({"question": question, "limit": 20})
                    self.assertFalse(result["isError"])
                    body = state["requests"][-1][2]
                    self.assertLessEqual(len(body), 32000)
                    self.assertEqual(json.loads(body.decode("utf-8")), {"question": question, "limit": 20})

    def test_invalid_arguments_never_call_network(self):
        cases = [{}, {"question": " "}, {"question": "x" * 4001}, {"question": "\ud800"},
                 {"question": "ok", "limit": True}, {"question": "ok", "limit": 0},
                 {"question": "ok", "limit": 21}, {"question": "ok", "other": 1}]
        with api() as state:
            for value in cases:
                with self.subTest(value=value):
                    self.assertTrue(call_ask_rag(value)["isError"])
            self.assertEqual(state["requests"], [])

    def test_configuration_restrictions_and_secret_file_rotation(self):
        invalid = ["http://localhost:8000", "https://127.0.0.1:8000", "http://127.0.0.1:0",
                   "http://127.0.0.1:99999", "http://user@127.0.0.1", "http://127.0.0.1/path",
                   "http://127.0.0.1?", "http://127.0.0.1#", "http://127.0.0.1:8\n"]
        with patch.dict(os.environ, {"MCP_RAG_READ_TOKEN": "secret-test"}, clear=True):
            for url in invalid:
                with self.subTest(url=url), patch.dict(os.environ, {"MCP_RAG_BASE_URL": url}):
                    with self.assertRaises(ValueError):
                        rag_config()
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "token"
            secret.write_bytes(b"first\r\n")
            with patch.dict(os.environ, {"MCP_RAG_BASE_URL": "http://127.0.0.1:8080", "MCP_RAG_READ_TOKEN_FILE": str(secret)}, clear=True):
                self.assertEqual(rag_config(), (8080, "first"))
                secret.write_bytes(b"second\n")
                self.assertEqual(rag_config(), (8080, "second"))
                with patch.dict(os.environ, {"MCP_RAG_READ_TOKEN": "other"}):
                    with self.assertRaises(ValueError):
                        rag_config()
                secret.write_bytes(b"x" * 16385)
                with self.assertRaises(ValueError):
                    rag_config()

    def test_error_http_statuses_redirect_no_retry_and_no_leaks(self):
        for status in (302, 400, 401, 403, 413, 415, 429, 500, 502, 503, 504):
            with self.subTest(status=status), api(status=status, payload=b"secret-test private-question", headers={"Location": "http://example.com/"}) as state:
                result = call_ask_rag({"question": "private-question"})
                self.assertTrue(result["isError"])
                self.assertEqual(len(state["requests"]), 1)
                self.assertNotIn("secret-test", str(result))
                self.assertNotIn("private-question", str(result))

    def test_bad_oversized_and_unsafe_responses(self):
        samples = [b"\xff", b"{", b'{"status":"no_context","status":"answered"}',
                   b'{"status":"answered","answer":"x","sources":[{"label":"x","document_id":"x","text":"x","chunk_index":0,"score":NaN}]}',
                   b'{"status":"no_context","answer":"\\ud800","sources":[]}',
                   json.dumps({**ANSWER, "sources": [{**ANSWER["sources"][0], "score": True}]}).encode(),
                   b" " * (MAX_RESPONSE_BYTES + 1)]
        for payload in samples:
            with self.subTest(sample=payload[:50]), api(payload=payload):
                self.assertTrue(call_ask_rag({"question": "x"})["isError"])
        with api(content_type="text/html"):
            self.assertTrue(call_ask_rag({"question": "x"})["isError"])

    def test_timeout(self):
        with api(delay=0.2), patch("mcp_lab.rag_client.DEFAULT_TOTAL_TIMEOUT_SECONDS", 0.05):
            result = call_ask_rag({"question": "x"})
        self.assertTrue(result["isError"])
        self.assertIn("tiempo", result["content"][0]["text"])

    def test_failed_calls_count_and_text_stats_remains_available(self):
        with patch.dict(os.environ, {}, clear=True):
            session = Session()
            session.state = "ready"
            def call(id_, name, arguments):
                return session.handle({"jsonrpc": "2.0", "id": id_, "method": "tools/call", "params": {"name": name, "arguments": arguments}})["result"]
            self.assertFalse(call(0, "text_stats", {"text": "hola"})["isError"])
            for i in range(1, MAX_TOOL_CALLS):
                self.assertTrue(call(i, "ask_rag", {"question": "x"})["isError"])
            self.assertIn("limit", call(MAX_TOOL_CALLS, "text_stats", {"text": "x"})["content"][0]["text"])

    def test_mcp_serialized_output_limit(self):
        # JSON puede expandirse al serializarse otra vez como texto MCP.
        answer = {"status": "no_context", "answer": "\\" * 70000, "sources": []}
        with api(payload=json.dumps(answer).encode()):
            session = Session()
            session.state = "ready"
            result = session.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ask_rag", "arguments": {"question": "x"}}})["result"]
        self.assertTrue(result["isError"])
        self.assertIn("tamaño", result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
