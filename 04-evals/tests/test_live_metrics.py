import contextlib
import hashlib
import http.client
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
from evals_lab import live_metrics

TOKEN = "synthetic_" + "x" * 32
REQUEST_ID = "a" * 32
ANSWERED = b'{"status":"answered","answer":"PRIVATE_ANSWER","sources":[{"document_id":"doc"}]}'
NO_CONTEXT = b'{"status":"no_context","answer":"PRIVATE_ANSWER","sources":[]}'


class FakeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        request = self.rfile.read(size)
        with self.server.lock:
            self.server.calls.append((self.path, self.headers.get("Authorization"), request))
            mode = self.server.modes.pop(0) if self.server.modes else "answered"
        if mode == "disconnect":
            self.close_connection = True
            return
        if mode == "bad_status":
            self.connection.sendall(b"NOT HTTP\r\n\r\n")
            self.close_connection = True
            return
        if mode == "timeout":
            time.sleep(1.4)
        if isinstance(mode, int):
            status, body = mode, b"PRIVATE_ERROR_BODY"
        else:
            status = 200
            body = {"answered":ANSWERED, "no_context":NO_CONTEXT, "timeout":ANSWERED,
                    "invalid_json":b'{"answer":', "duplicate_json":b'{"status":"answered","status":"no_context"}',
                    "nan":b'{"status":"no_context","answer":"x","sources":[],"ignored":NaN}',
                    "bad_source":b'{"status":"answered","answer":"x","sources":[{"document_id":"bad id"}]}',
                    "bad_coherence":b'{"status":"no_context","answer":"x","sources":[{"document_id":"a"}]}',
                    "huge_number":b'{"status":"no_context","answer":"x","sources":[],"ignored":' + b'9'*129 + b'}',
                    "bom":b'\xef\xbb\xbf' + NO_CONTEXT}.get(mode, ANSWERED)
        self.send_response(status)
        if mode == "missing_length":
            pass
        elif mode == "duplicate_length":
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Length", str(len(body)))
        elif mode == "transfer":
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Content-Length", str(len(body)))
        elif mode == "bad_length":
            self.send_header("Content-Length", "not-a-number")
        elif mode == "long_length":
            self.send_header("Content-Length", "0" * 11)
        elif mode == "oversize":
            self.send_header("Content-Length", "2000001")
        elif mode == "truncated":
            self.send_header("Content-Length", str(len(body) + 10))
        else:
            self.send_header("Content-Length", str(len(body)))
        if mode == "duplicate_request_id":
            self.send_header("X-Request-ID", REQUEST_ID)
            self.send_header("X-Request-ID", REQUEST_ID)
        elif mode == "bad_request_id":
            self.send_header("X-Request-ID", "PRIVATE_HEADER")
        else:
            self.send_header("X-Request-ID", REQUEST_ID)
        self.send_header("Connection", "close")
        try:
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass
        self.close_connection = True


@contextlib.contextmanager
def fake_server(modes):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
    server.daemon_threads = True
    server.lock = threading.Lock()
    server.calls = []
    server.modes = list(modes)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def jsonl(records):
    return b"".join((json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
                    for record in records)


class LiveMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="evals-live-metrics-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.assertEqual(self.folder.parent, Path(tempfile.gettempdir()).resolve())
        self.cases = self.folder / "private-cases.jsonl"
        self.context = self.folder / "private-context.json"
        self.cases.write_bytes(jsonl([{"id":"q1","input":"PRIVATE_QUESTION_1"},
                                      {"id":"q2","input":"PRIVATE_QUESTION_2"}]))
        self.context_data = {"schema_version":1, "embedding_provider":"simulated", "embedding_model":"unknown",
                             "generation_provider":"simulated", "generation_model":"unknown",
                             "corpus_revision":"synthetic-v1", "server_revision":"fixture-v1",
                             "hardware_note":"unknown", "min_score":None}
        self.context.write_bytes(json.dumps(self.context_data, separators=(",", ":")).encode("utf-8"))
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1", RAG_READ_TOKEN=TOKEN)

    def command(self, port, *extra):
        return [sys.executable, "-S", "-m", "evals_lab.live_metrics", "--cases", str(self.cases),
                "--context", str(self.context), "--port", str(port), *extra]

    def run_cli(self, port, *extra, env=None):
        return subprocess.run(self.command(port, *extra), cwd=self.folder, env=env or self.env,
                              capture_output=True, timeout=20)

    def test_loopback_measurement_order_warmup_and_redaction(self):
        with fake_server(["no_context", "answered", "no_context", "answered", "no_context"]) as server:
            result = self.run_cli(server.server_port, "--warmup", "1", "--repeats", "2", "--limit", "2")
            self.assertEqual((result.returncode, result.stderr), (0, b""))
            report = json.loads(result.stdout)
            self.assertEqual((report["run_status"], report["abort_reason"]), ("completed", None))
            self.assertEqual((report["warmup"]["planned"], report["warmup"]["attempted"]), (1, 1))
            measured = report["measurement"]
            self.assertEqual((measured["planned"], measured["attempted"], measured["not_started"]), (4, 4, 0))
            self.assertEqual((measured["outcome_counts"]["ok_answered"], measured["outcome_counts"]["ok_no_context"]), (2, 2))
            self.assertEqual(measured["failure_rate_observed"], 0)
            self.assertEqual(measured["latency_ns"]["count"], 4)
            self.assertIsNone(measured["latency_ns"]["p95_ns"])
            self.assertEqual([item["case_id"] for item in measured["results"]], ["q1","q2","q1","q2"])
            self.assertEqual([item["repetition"] for item in measured["results"]], [1,1,2,2])
            self.assertTrue(all(item["request_id"] == REQUEST_ID and item["duration_ns"] >= 0 for item in measured["results"]))
            self.assertEqual(len(server.calls), 5)
            self.assertTrue(all(path == "/v1/answers" and auth == "Bearer " + TOKEN for path, auth, _ in server.calls))
            self.assertEqual([json.loads(raw)["question"] for _,_,raw in server.calls],
                             ["PRIVATE_QUESTION_1", "PRIVATE_QUESTION_1", "PRIVATE_QUESTION_2", "PRIVATE_QUESTION_1", "PRIVATE_QUESTION_2"])
            for private in (TOKEN.encode(), b"PRIVATE_QUESTION", b"PRIVATE_ANSWER", b"private-"):
                self.assertNotIn(private, result.stdout + result.stderr)

    def test_invalid_context_cases_and_limits_cause_no_connections(self):
        original_cases, original_context = self.cases.read_bytes(), self.context.read_bytes()
        contexts = []
        for key, value in (("schema_version",True), ("embedding_provider","openai"), ("min_score",True),
                           ("min_score",1.1), ("min_score",float("nan")), ("hardware_note","bad\nline")):
            context = dict(self.context_data); context[key] = value
            contexts.append(json.dumps(context).encode("utf-8"))
        contexts += [b'\xef\xbb\xbf{}', b'\xff', b'{"schema_version":1,"schema_version":1}', b'x' * 65537]
        with fake_server([]) as server:
            for data in contexts:
                with self.subTest(context=data[:80]):
                    self.context.write_bytes(data); result = self.run_cli(server.server_port)
                    self.assertEqual((result.returncode, result.stdout), (2, b""))
                    self.assertNotIn(b"private-", result.stderr)
            self.context.write_bytes(original_context)
            for data in (b"", jsonl([{"id":"bad id","input":"x"}]), jsonl([{"id":"x","input":"  "}]),
                         jsonl([{"id":"x","input":"x"}]*2), jsonl([{"id":"q"+str(i),"input":"x"} for i in range(31)])):
                with self.subTest(cases=data[:80]):
                    self.cases.write_bytes(data); self.assertEqual(self.run_cli(server.server_port).returncode, 2)
            self.cases.write_bytes(original_cases)
            for arguments in (("--port", "0"), ("--port", "65536"), ("--repeats", "6"), ("--warmup", "3"),
                              ("--limit", "0"), ("--timeout-s", "181"), ("--timeout-s", "0")):
                self.assertEqual(self.run_cli(server.server_port, *arguments).returncode, 2)
            self.cases.write_bytes(jsonl([{"id":"q"+str(i),"input":"x"} for i in range(16)]))
            self.assertEqual(self.run_cli(server.server_port, "--repeats", "2").returncode, 2)
            bad_env = dict(self.env, RAG_READ_TOKEN="PRIVATE_INVALID_TOKEN\n")
            invalid = self.run_cli(server.server_port, env=bad_env)
            self.assertEqual(invalid.returncode, 2)
            self.assertNotIn(b"PRIVATE_INVALID_TOKEN", invalid.stderr)
            context = dict(self.context_data, hardware_note=TOKEN)
            self.context.write_bytes(json.dumps(context).encode("utf-8"))
            self.assertEqual(self.run_cli(server.server_port).returncode, 2)
            self.context.write_bytes(original_context)
            self.cases.write_bytes(jsonl([{"id":REQUEST_ID,"input":"x"}]))
            self.assertEqual(self.run_cli(server.server_port, env=dict(self.env, RAG_READ_TOKEN=REQUEST_ID)).returncode, 2)
            self.cases.write_bytes(original_cases)
            hash_token = hashlib.sha256(original_cases).hexdigest()[:32]
            self.assertEqual(self.run_cli(server.server_port, env=dict(self.env, RAG_READ_TOKEN=hash_token)).returncode, 2)
            self.assertEqual(server.calls, [])

    def test_http_framing_body_and_status_classification(self):
        scenarios = [
            ("missing_length", "framing_error", True), ("duplicate_length", "framing_error", True),
            ("transfer", "framing_error", True), ("bad_length", "framing_error", True),
            ("long_length", "framing_error", True), ("bad_status", "framing_error", True),
            ("oversize", "response_too_large", True), ("truncated", "transport_error", True),
            ("disconnect", "transport_error", True), ("invalid_json", "invalid_response", False),
            ("duplicate_json", "invalid_response", False), ("nan", "invalid_response", False),
            ("huge_number", "invalid_response", False), ("bom", "invalid_response", False),
            ("bad_source", "invalid_response", False), ("bad_coherence", "invalid_response", False),
            (400, "http_error", False), (504, "http_error", True),
        ]
        for mode, outcome, stops in scenarios:
            with self.subTest(mode=mode), fake_server([mode, "answered"]) as server:
                result = self.run_cli(server.server_port)
                self.assertEqual(result.returncode, 1, result.stderr)
                report = json.loads(result.stdout); measured = report["measurement"]
                self.assertEqual(measured["results"][0]["outcome"], outcome)
                self.assertEqual(measured["attempted"], 1 if stops else 2)
                self.assertEqual(measured["not_started"], 1 if stops else 0)
                self.assertEqual(report["run_status"], "aborted_policy" if stops else "completed")
                if mode == 504:
                    self.assertEqual((report["abort_reason"], measured["results"][0]["http_status"]), ("http_504", 504))
                for private in (TOKEN.encode(), b"PRIVATE_ERROR_BODY", b"PRIVATE_ANSWER", b"private-"):
                    self.assertNotIn(private, result.stdout + result.stderr)

    def test_timeout_and_warmup_abort_accounting(self):
        with fake_server(["timeout", "answered"]) as server:
            result = self.run_cli(server.server_port, "--timeout-s", "1")
            report = json.loads(result.stdout)
            self.assertEqual(result.returncode, 1)
            self.assertEqual((report["abort_reason"], report["measurement"]["attempted"],
                              report["measurement"]["not_started"]), ("timeout", 1, 1))
            self.assertEqual(len(server.calls), 1)
            self.assertIsNone(report["measurement"]["latency_ns"]["p50_ns"])
        with fake_server([503, "answered"]) as server:
            result = self.run_cli(server.server_port, "--warmup", "2")
            report = json.loads(result.stdout)
            self.assertEqual(result.returncode, 1)
            self.assertEqual((report["run_status"], report["abort_reason"]), ("aborted_warmup", "http_503"))
            self.assertEqual((report["warmup"]["planned"], report["warmup"]["attempted"], report["warmup"]["not_started"]), (2,1,1))
            self.assertEqual((report["measurement"]["attempted"], report["measurement"]["not_started"]), (0,2))
            self.assertIsNone(report["measurement"]["failure_rate_observed"])
            self.assertEqual(len(server.calls), 1)

    def test_request_id_validation_and_http_stop_policies(self):
        for mode in ("duplicate_request_id", "bad_request_id"):
            with self.subTest(mode=mode), fake_server([mode, "answered"]) as server:
                result = self.run_cli(server.server_port)
                self.assertEqual(result.returncode, 0)
                self.assertIsNone(json.loads(result.stdout)["measurement"]["results"][0]["request_id"])
                self.assertNotIn(b"PRIVATE_HEADER", result.stdout + result.stderr)
        with fake_server(["answered", "answered"]) as server:
            result = self.run_cli(server.server_port, env=dict(self.env, RAG_READ_TOKEN=REQUEST_ID))
            self.assertEqual(result.returncode, 0)
            self.assertTrue(all(event["request_id"] is None for event in json.loads(result.stdout)["measurement"]["results"]))
            self.assertNotIn(REQUEST_ID.encode(), result.stdout + result.stderr)
        for status in (401,403,429,503,504):
            with self.subTest(status=status), fake_server([status, "answered"]) as server:
                result = self.run_cli(server.server_port)
                report = json.loads(result.stdout)
                self.assertEqual((result.returncode,report["abort_reason"],report["measurement"]["attempted"]), (1,"http_"+str(status),1))

    def test_percentiles_and_abort_with_injected_events(self):
        self.assertEqual(live_metrics._distribution([]), {"count":0,"min_ns":None,"max_ns":None,"p50_ns":None,"p95_ns":None})
        sample = live_metrics._distribution([50,120,180,220])
        self.assertEqual((sample["p50_ns"],sample["p95_ns"]),(120,None))
        self.assertIsNone(live_metrics._distribution(list(range(1,20)))["p95_ns"])
        self.assertEqual(live_metrics._distribution(list(range(1,21)))["p95_ns"],19)
        cases = [{"id":"q"+str(i),"input":"x"} for i in range(7)]
        outcomes = [("ok_answered",50,200),("ok_answered",120,200),("ok_answered",180,200),
                    ("ok_no_context",220,200),("http_error",60,400),("timeout",6100,None)]
        events = [{"outcome":o,"duration_ns":n,"http_status":s,"request_id":None} for o,n,s in outcomes]
        with patch.object(live_metrics,"perform_attempt",side_effect=events) as perform:
            report=live_metrics.run(cases,context={},cases_sha256="",context_sha256="",token=TOKEN,
                                    port=1234,limit=2,repeats=1,warmup=0,timeout_s=5)
        measured=report["measurement"]
        self.assertEqual((perform.call_count,measured["planned"],measured["attempted"],measured["not_started"]),(6,7,6,1))
        self.assertEqual(measured["failure_rate_observed"],2/6)
        self.assertEqual((measured["latency_ns"]["count"],measured["latency_ns"]["p50_ns"]),(4,120))
        self.assertEqual((measured["answered_latency_ns"]["count"],measured["no_context_latency_ns"]["count"]),(3,1))
        self.assertEqual(report["abort_reason"],"timeout")
        with patch.object(live_metrics,"perform_attempt",return_value=dict(events[-1])):
            last=live_metrics.run(cases[:1],context={},cases_sha256="",context_sha256="",token=TOKEN,
                                  port=1234,limit=2,repeats=1,warmup=0,timeout_s=5)
        self.assertEqual((last["run_status"],last["measurement"]["not_started"]),("aborted_policy",0))

    def test_clock_boundary_and_unexpected_error_sanitization(self):
        response = type("Response", (), {"status":200,"headers":http.client.parse_headers(io.BytesIO(
            b"Content-Length: " + str(len(NO_CONTEXT)).encode() + b"\r\n\r\n")),"read":lambda self,n:NO_CONTEXT})()
        class Connection:
            def __init__(self,*args,**kwargs):
                self.args,self.kwargs=args,kwargs
            def request(self,*args): self.request_args=args
            def getresponse(self): return response
            def close(self): pass
        with patch.object(live_metrics.time,"monotonic_ns",side_effect=[100,250]), patch.object(live_metrics.http.client,"HTTPConnection",Connection):
            event=live_metrics.perform_attempt(b"{}",TOKEN,port=1234,timeout_s=5,limit=2)
        self.assertEqual((event["duration_ns"],event["outcome"]),(150,"ok_no_context"))
        errors=io.StringIO()
        with patch.object(live_metrics,"load_context",side_effect=RuntimeError("PRIVATE_FAILURE")), patch.object(sys,"stderr",errors):
            self.assertEqual(live_metrics.main(["--cases",str(self.cases),"--context",str(self.context),"--port","1234"]),2)
        self.assertNotIn("PRIVATE_FAILURE",errors.getvalue())

    def test_help_and_closed_stdout(self):
        help_result=subprocess.run([sys.executable,"-S","-m","evals_lab.live_metrics","--help"],env=self.env,capture_output=True,timeout=10)
        self.assertEqual(help_result.returncode,0)
        with fake_server(["answered","answered"]) as server:
            process=subprocess.Popen(self.command(server.server_port),cwd=self.folder,env=self.env,
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            process.stdout.close(); process.stdout=None
            _,stderr=process.communicate(timeout=15)
            self.assertEqual(process.returncode,2,stderr)
            self.assertNotIn(b"Traceback",stderr)


if __name__ == "__main__":
    unittest.main()
