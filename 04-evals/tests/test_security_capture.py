import contextlib
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "synthetic_" + "x" * 32
RID = "a" * 32


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.calls.append((self.path, self.headers.get("Authorization"), json.loads(raw)))
        mode = self.server.mode
        if mode == "disconnect":
            self.close_connection = True
            return
        if mode == "timeout":
            time.sleep(1.4)
        fixture = self.server.fixture
        sources = [{"label": f"[{i}]", **s, "score": .5} for i, s in enumerate(fixture["sources"], 1)]
        body = {"status": fixture["status"], "answer": fixture["answer"], "sources": sources}
        if mode == "no_context": body = {"status": "no_context", "answer": "Sin contexto", "sources": []}
        if mode == "leak": body["answer"] = TOKEN
        if mode == "duplicate_source": body["sources"].append(dict(body["sources"][0], label="[2]"))
        if mode == "bad_label": body["sources"][0]["label"] = "[99]"
        if mode == "bad_index": body["sources"][0]["chunk_index"] = True
        if mode == "inconsistent": body["status"] = "no_context"
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if mode == "invalid_json": encoded = b'{"status":'
        if mode == "duplicate_json": encoded = b'{"status":"answered","status":"no_context"}'
        if mode == "http_error": encoded = b"PRIVATE_ERROR_BODY"
        self.send_response(500 if mode == "http_error" else 200)
        if mode == "duplicate_length":
            self.send_header("Content-Length", str(len(encoded)))
        if mode == "transfer": self.send_header("Transfer-Encoding", "chunked")
        if mode != "missing_length":
            length = 2_000_001 if mode == "oversize" else len(encoded) + (5 if mode == "truncated" else 0)
            self.send_header("Content-Length", str(length))
        self.send_header("X-Request-ID", RID)
        self.send_header("Connection", "close")
        try:
            self.end_headers()
            self.wfile.write(encoded)
        except OSError:
            pass
        self.close_connection = True


@contextlib.contextmanager
def server(mode, fixture):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    httpd.mode, httpd.fixture, httpd.calls = mode, fixture, []
    thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="security-capture-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.cases = self.folder / "cases.jsonl"
        self.cases.write_bytes((ROOT / "examples/security_cases.jsonl").read_bytes())
        self.fixtures = [json.loads(s) for s in (ROOT / "examples/security_outputs.jsonl").read_text(encoding="utf-8").splitlines()]
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1", RAG_READ_TOKEN=TOKEN)

    def run_cli(self, port, *extra, env=None, case_id="orden_directa"):
        cmd = [sys.executable, "-S", "-m", "evals_lab.security_capture", "--cases", str(self.cases),
               "--case-id", case_id, "--port", str(port), *extra]
        return subprocess.run(cmd, cwd=self.folder, env=self.env if env is None else env, capture_output=True, timeout=15)

    def test_full_capture_and_review_binding(self):
        with server("answered", self.fixtures[1]) as httpd:
            result = self.run_cli(httpd.server_port)
            self.assertEqual(result.returncode, 0, result.stderr)
            output, report = json.loads(result.stdout), json.loads(result.stderr)
            self.assertEqual(output, self.fixtures[1])
            self.assertEqual(report["outcome"], "ok_answered")
            self.assertEqual((report["planned"], report["attempted"], report["not_started"]), (1, 1, 0))
            self.assertEqual(report["request_id"], RID)
            self.assertEqual(report["output_line_sha256"], hashlib.sha256(result.stdout.rstrip(b"\n")).hexdigest())
            self.assertEqual(report["cases_sha256"], hashlib.sha256(self.cases.read_bytes()).hexdigest())
            self.assertEqual(len(httpd.calls), 1)
            path, auth, request = httpd.calls[0]
            self.assertEqual((path, auth), ("/v1/answers", "Bearer " + TOKEN))
            self.assertEqual(request["limit"], 2)
            self.assertNotIn(TOKEN.encode(), result.stdout + result.stderr)
        outpath = self.folder / "outputs.jsonl"; outpath.write_bytes(result.stdout)
        lines = (ROOT / "examples/security_reviews.jsonl").read_bytes().splitlines()
        review = next(line for line in lines if json.loads(line)["id"] == "orden_directa")
        revpath = self.folder / "reviews.jsonl"; revpath.write_bytes(review + b"\n")
        cmd = [sys.executable, "-S", "-m", "evals_lab.security", "--cases", str(self.cases),
               "--outputs", str(outpath), "--reviews", str(revpath)]
        evaluated = subprocess.run(cmd, cwd=self.folder, env=self.env, capture_output=True, timeout=15)
        self.assertEqual(evaluated.returncode, 1, evaluated.stderr)  # other cases not captured
        self.assertEqual(json.loads(evaluated.stdout)["followed"], 1)

    def test_failures_are_errors_and_no_retry(self):
        expected = {"http_error":"http_error", "missing_length":"framing_error", "duplicate_length":"framing_error",
                    "transfer":"framing_error", "oversize":"response_too_large", "truncated":"transport_error",
                    "disconnect":"transport_error", "invalid_json":"invalid_response", "duplicate_json":"invalid_response",
                    "leak":"invalid_response", "duplicate_source":"invalid_response", "bad_label":"invalid_response",
                    "bad_index":"invalid_response", "inconsistent":"invalid_response", "timeout":"timeout"}
        for mode, outcome in expected.items():
            with self.subTest(mode=mode), server(mode, self.fixtures[1]) as httpd:
                result = self.run_cli(httpd.server_port, "--timeout-s", "1")
                self.assertEqual(result.returncode, 1, result.stderr)
                output, report = json.loads(result.stdout), json.loads(result.stderr)
                self.assertEqual((output["status"], output["sources"], report["outcome"]), ("error", None, outcome))
                self.assertEqual(len(httpd.calls), 1)
                self.assertNotIn(TOKEN.encode(), result.stdout + result.stderr)
                self.assertNotIn(b"PRIVATE_ERROR_BODY", result.stdout + result.stderr)

    def test_no_context_is_valid_and_other_cases_not_selected(self):
        with server("no_context", self.fixtures[1]) as httpd:
            result = self.run_cli(httpd.server_port)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stderr)["outcome"], "ok_no_context")
        self.assertEqual(json.loads(result.stdout)["sources"], [])

    def test_bad_inputs_do_not_connect(self):
        with server("answered", self.fixtures[1]) as httpd:
            bad_env = dict(self.env, RAG_READ_TOKEN="short")
            for result in (self.run_cli(httpd.server_port, env=bad_env),
                           self.run_cli(httpd.server_port, case_id="not-found"),
                           self.run_cli(httpd.server_port, "--limit", "0")):
                self.assertEqual((result.returncode, result.stdout), (2, b""))
            self.assertEqual(httpd.calls, [])
        data = self.cases.read_text(encoding="utf-8").replace("¿Dónde", TOKEN + "¿Dónde", 1)
        self.cases.write_text(data, encoding="utf-8")
        result = self.run_cli(1)
        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertNotIn(TOKEN.encode(), result.stderr)


if __name__ == "__main__":
    unittest.main()
