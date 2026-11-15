import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mcp_lab import rag_client, supervisor
from mcp_lab.protocol import Session
from mcp_lab.server import serve
from test_rag import ANSWER, api


class SlowServer(ThreadingHTTPServer):
    def __init__(self, mode):
        self.mode = mode
        self.started = threading.Event()
        self.continued = threading.Event()
        self.request_count = 0
        super().__init__(("127.0.0.1", 0), SlowHandler)


class SlowHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.server.request_count += 1
        self.server.started.set()
        body = json.dumps({"status": "no_context", "answer": "sin contexto", "sources": []}).encode()
        header = ("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)) + "\r\n\r\n").encode()
        try:
            if self.server.mode == "continues":
                time.sleep(1.2)
                self.server.continued.set()
                self.connection.sendall(header + body)
            elif self.server.mode == "boundary":
                time.sleep(0.7)
                self.connection.sendall(header + body)
            elif self.server.mode == "headers":
                for byte in header:
                    self.connection.sendall(bytes([byte]))
                    time.sleep(0.02)
            else:
                self.connection.sendall(header)
                for byte in body:
                    self.connection.sendall(bytes([byte]))
                    time.sleep(0.02)
        except OSError:
            pass

    def log_message(self, *_args):
        pass


class DeadlineTests(unittest.TestCase):
    def run_slow(self, mode, configured_timeout=None):
        server = SlowServer(mode)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            env = {"MCP_RAG_BASE_URL": f"http://127.0.0.1:{server.server_port}", "MCP_RAG_READ_TOKEN": "secret-test"}
            with patch.dict(os.environ, env), patch.object(rag_client, "DEFAULT_TOTAL_TIMEOUT_SECONDS", 0.8):
                if configured_timeout is None:
                    os.environ.pop("MCP_RAG_TOTAL_TIMEOUT_SECONDS", None)
                else:
                    os.environ["MCP_RAG_TOTAL_TIMEOUT_SECONDS"] = configured_timeout
                os.environ.pop("MCP_RAG_READ_TOKEN_FILE", None)
                session = Session()
                session.state = "ready"
                started = time.monotonic()
                result = session.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": "ask_rag", "arguments": {"question": "pregunta privada"}}})["result"]
                elapsed = time.monotonic() - started
                self.assertTrue(server.started.is_set(), "La consulta debe llegar al servidor de prueba")
                self.assertTrue(result["isError"])
                self.assertIn("tiempo máximo", result["content"][0]["text"])
                self.assertNotIn("secret-test", str(result))
                self.assertNotIn("pregunta privada", str(result))
                self.assertEqual(server.request_count, 1, "No debe haber reintentos automáticos")
                self.assertGreaterEqual(elapsed, 0.6)
                self.assertLess(elapsed, 4)
                after = session.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                        "params": {"name": "text_stats", "arguments": {"text": "hola"}}})["result"]
                self.assertFalse(after["isError"])
                if mode == "continues":
                    self.assertTrue(server.continued.wait(3), "El backend puede seguir trabajando después del timeout")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_total_deadline_for_slow_headers_and_body(self):
        for mode in ("headers", "body"):
            with self.subTest(mode=mode):
                self.run_slow(mode)

    def test_backend_may_continue_after_local_timeout(self):
        self.run_slow("continues")

    def test_configured_deadline_is_enforced(self):
        self.run_slow("continues", configured_timeout="1")

    def test_result_near_deadline_produces_one_response(self):
        server = SlowServer("boundary")
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "ask_rag", "arguments": {"question": "hola"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ]
        data = b"".join((json.dumps(message) + "\n").encode() for message in messages)
        try:
            env = {"MCP_RAG_BASE_URL": f"http://127.0.0.1:{server.server_port}", "MCP_RAG_READ_TOKEN": "secret-test"}
            with patch.dict(os.environ, env), patch.object(rag_client, "DEFAULT_TOTAL_TIMEOUT_SECONDS", 0.8):
                os.environ.pop("MCP_RAG_TOTAL_TIMEOUT_SECONDS", None)
                os.environ.pop("MCP_RAG_READ_TOKEN_FILE", None)
                output = io.BytesIO()
                self.assertEqual(serve(io.BytesIO(data), output, io.StringIO()), 0)
            responses = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual([response["id"] for response in responses], [1, 2, 3])
            result = responses[1]["result"]
            if result["isError"]:
                self.assertIn("tiempo máximo", result["content"][0]["text"])
            else:
                self.assertEqual(json.loads(result["content"][0]["text"])["status"], "no_context")
            self.assertEqual(server.request_count, 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_invalid_deadline_never_starts_worker(self):
        for value in ("", "0", "601", "-1", "1.5", " 1", "1e2", "0001"):
            with self.subTest(value=value), patch.dict(os.environ, {"MCP_RAG_TOTAL_TIMEOUT_SECONDS": value}), \
                    patch.object(supervisor, "_worker_command") as command:
                result = rag_client.call_ask_rag({"question": "hola"})
                self.assertTrue(result["isError"])
                command.assert_not_called()

    def test_both_deadline_boundaries_are_accepted(self):
        for value in ("1", "600"):
            with self.subTest(value=value), patch.dict(os.environ, {"MCP_RAG_TOTAL_TIMEOUT_SECONDS": value}), \
                    patch.object(supervisor, "run_rag_worker", return_value={"content": [], "isError": False}) as call:
                rag_client.call_ask_rag({"question": "hola"})
                self.assertEqual(call.call_count, 1)
                remaining = call.call_args.args[2] - time.monotonic()
                self.assertGreater(remaining, int(value) - 1)
                self.assertLessEqual(remaining, int(value))

    def run_fake_worker(self, code, arguments=None, timeout=0.8, config=None):
        processes = []
        real_popen = subprocess.Popen
        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process
        with patch.object(supervisor, "_worker_command", return_value=[sys.executable, "-u", "-c", code]), \
                patch.object(supervisor.subprocess, "Popen", side_effect=spawn):
            result = supervisor.run_rag_worker(arguments or {"question": "hola"}, config or {}, time.monotonic() + timeout)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll(), "El auxiliar debe quedar recogido")
        return result

    def test_worker_that_does_not_read_stdin_is_terminated(self):
        start = time.monotonic()
        result = self.run_fake_worker("import time; time.sleep(30)", arguments={"question": "x" * 150000})
        self.assertTrue(result["isError"])
        self.assertIn("tiempo máximo", result["content"][0]["text"])
        self.assertLess(time.monotonic() - start, 4)

    def test_oversized_or_malformed_worker_output_is_rejected(self):
        codes = [
            "import struct,sys,time; sys.stdout.buffer.write(struct.pack('!I',262145)); sys.stdout.buffer.flush(); time.sleep(30)",
            "import struct,sys; d=b'{not-json}'; sys.stdout.buffer.write(struct.pack('!I',len(d))+d); sys.stdout.buffer.flush()",
            "import os; os._exit(23)",
        ]
        for code in codes:
            with self.subTest(code=code):
                result = self.run_fake_worker(code, timeout=4)
                self.assertTrue(result["isError"])
                self.assertEqual(result["content"][0]["text"], supervisor.WORKER_ERROR)

    def test_sensitive_configuration_is_not_in_worker_environment(self):
        code = """import json,os,struct,sys
raw=sys.stdin.buffer.read()
n=struct.unpack('!I',raw[:4])[0]
request=json.loads(raw[4:4+n])
ok=('MCP_RAG_READ_TOKEN' not in os.environ and 'OPENAI_API_KEY' not in os.environ and request['config']['MCP_RAG_READ_TOKEN']=='private-test-token')
d=json.dumps({'content':[{'type':'text','text':'clean' if ok else 'bad'}],'isError':False}).encode()
sys.stdout.buffer.write(struct.pack('!I',len(d))+d)
"""
        with patch.dict(os.environ, {"MCP_RAG_READ_TOKEN": "private-test-token", "OPENAI_API_KEY": "private-api-key"}):
            result = self.run_fake_worker(code, config={"MCP_RAG_READ_TOKEN": "private-test-token"}, timeout=4)
        self.assertEqual(result["content"][0]["text"], "clean")
        self.assertNotIn("private-test-token", str(result))
        self.assertNotIn("private-api-key", str(result))

    def test_cleanup_failure_ends_session_without_details(self):
        input_stream = io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
        output_stream, error_stream = io.BytesIO(), io.StringIO()
        with patch("mcp_lab.server.Session") as session:
            session.return_value.handle.side_effect = supervisor.WorkerCleanupError("private")
            self.assertEqual(serve(input_stream, output_stream, error_stream), 1)
        self.assertEqual(output_stream.getvalue(), b"")
        self.assertNotIn("private", error_stream.getvalue())

    def test_zipapp_executes_worker_with_rag_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "mcp.pyz"
            subprocess.run([sys.executable, str(ROOT / "build.py"), "--output", str(archive)],
                           capture_output=True, check=True, timeout=10)
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "ask_rag", "arguments": {"question": "hola"}}},
            ]
            data = b"".join((json.dumps(item) + "\n").encode() for item in messages)
            with api() as state:
                env = os.environ.copy()
                env.pop("PYTHONPATH", None)
                env.pop("MCP_RAG_TOTAL_TIMEOUT_SECONDS", None)
                completed = subprocess.run([sys.executable, str(archive)], input=data, capture_output=True,
                                           cwd=directory, env=env, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            response = json.loads(completed.stdout.splitlines()[-1])["result"]
            self.assertFalse(response["isError"])
            self.assertEqual(json.loads(response["content"][0]["text"]), ANSWER)
            self.assertEqual(len(state["requests"]), 1)

    def test_worker_exits_after_parent_disappears(self):
        started, closed = threading.Event(), threading.Event()

        class WatchHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                started.set()
                self.connection.settimeout(6)
                try:
                    if not self.connection.recv(1):
                        closed.set()
                except socket.timeout:
                    pass
                except (ConnectionError, OSError):
                    closed.set()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), WatchHandler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        script = """import json,os,struct,subprocess
from mcp_lab.supervisor import _worker_command,_worker_env
data=json.dumps({'arguments':{'question':'hola'},'config':{'MCP_RAG_BASE_URL':os.environ['TEST_RAG_URL'],'MCP_RAG_READ_TOKEN':'fixture-token'},'remaining':0.25}).encode()
p=subprocess.Popen(_worker_command(),stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=_worker_env())
p.stdin.write(struct.pack('!I',len(data))+data)
p.stdin.close()
os._exit(0)
"""
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            env["TEST_RAG_URL"] = f"http://127.0.0.1:{server.server_port}"
            completed = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(started.wait(5), "El auxiliar debe alcanzar el servidor")
            self.assertTrue(closed.wait(6), "El auxiliar debe cerrar la conexión tras desaparecer el padre")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
