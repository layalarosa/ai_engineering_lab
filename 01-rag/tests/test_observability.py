import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.database_cli import main
from rag.ingestion import DocumentChunk
from rag.observability import Trace
from rag.search import SearchResult


class TraceTests(unittest.TestCase):
    def test_trace_success_failure_and_no_exception_content(self):
        stream = io.StringIO()
        trace = Trace(True, stream)
        with trace.step("embed", provider="ollama"):
            pass
        with self.assertRaisesRegex(ValueError, "secret"):
            with trace.step("retrieve"):
                raise ValueError("secret")
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([event["status"] for event in events], ["ok", "error"])
        self.assertEqual(events[0]["run_id"], events[1]["run_id"])
        self.assertGreaterEqual(events[0]["duration_ms"], 0)
        self.assertNotIn("secret", stream.getvalue())

    def test_trace_disabled(self):
        stream = io.StringIO()
        with Trace(False, stream).step("embed"):
            pass
        self.assertEqual(stream.getvalue(), "")

    def test_cli_records_stages_without_question_or_document(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        psycopg = SimpleNamespace(connect=MagicMock(return_value=conn), Error=type("FakeDbError", (Exception,), {}))
        result = SearchResult(DocumentChunk("private-file", 0, "private-document"), 1.0)
        stderr, stdout = io.StringIO(), io.StringIO()
        argv = ["rag", "search", "private-question", "--model", "example", "--observe"]
        with patch.dict(sys.modules, {"psycopg": psycopg}), patch.dict("os.environ", {"DATABASE_URL": "secret-dsn"}), \
             patch.object(sys, "argv", argv), patch("rag.database_cli.embed", return_value=[[1.0]]), \
             patch("rag.database_cli.search", return_value=[result]), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(main(), 0)
        events = [json.loads(line) for line in stderr.getvalue().splitlines()]
        self.assertEqual([e["stage"] for e in events], ["connect_db", "embed", "retrieve"])
        for secret in ("private-question", "private-document", "private-file", "secret-dsn"):
            self.assertNotIn(secret, stderr.getvalue())
        self.assertIn("private-document", stdout.getvalue())
