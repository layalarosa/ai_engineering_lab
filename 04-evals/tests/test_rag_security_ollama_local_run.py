import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.request import Request

FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(FOLDER))
spec = importlib.util.spec_from_file_location("rag_security_ollama_local_run", FOLDER / "rag_security_ollama_local_run.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class LocalRunTests(unittest.TestCase):
    def test_only_local_ollama_endpoints_are_allowed(self):
        opener = MagicMock()
        request = Request("http://127.0.0.1:11434/api/embed", data=b"{}")
        check.checked_urlopen(opener, request, timeout=3)
        opener.open.assert_called_once_with(request, timeout=3)
        for url in ("http://localhost:11434/api/embed", "http://example.com:11434/api/embed",
                    "http://127.0.0.1:1/api/embed", "http://127.0.0.1:11434/api/tags",
                    "http://user@127.0.0.1:11434/api/embed", "http://127.0.0.1:11434/api/embed?x=1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                check.checked_urlopen(opener, Request(url), timeout=3)

    def test_model_inventory_requires_exact_digests(self):
        entries = [{"name":check.EMBED_MODEL, "digest":"a" * 64}, {"name":check.GEN_MODEL, "digest":"b" * 64}]
        def opener_for(models):
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps({"models":models}).encode()
            opener = MagicMock(); opener.open.return_value = response
            return opener
        self.assertEqual(check.model_digests(opener_for(entries)), {check.EMBED_MODEL:"a" * 64, check.GEN_MODEL:"b" * 64})
        for models in (entries[:1], entries + entries[:1], [dict(entries[0], digest="invalid"), entries[1]]):
            with self.subTest(models=models), self.assertRaises(ValueError):
                check.model_digests(opener_for(models))

    def test_failure_preserves_error_and_remaining_cases(self):
        cases, _, _, _ = check.sim.load_inputs(check.ROOT / "04-evals/examples/security_cases.jsonl",
                                               check.ROOT / "04-evals/examples/security_outputs.jsonl")
        records = [{"id":c["id"], "status":"not_started", "stage":None, "error":None,
                    "capture_started":False, "output_saved":False, "api_cleanup":"not_created",
                    "attack_source_exposed":None} for c in cases]
        def work(case, dbname, params, record, output_path, trace_stream):
            record["api_cleanup"] = "confirmed"
            record["stage"] = "capture"
            raise RuntimeError("private failure")
        with tempfile.TemporaryDirectory() as temp:
            out, events = Path(temp) / "outputs", Path(temp) / "events"
            out.write_bytes(b""); events.write_bytes(b"")
            with patch.object(check, "run_case", side_effect=work):
                rows = check.execute(cases, records, {}, out, events, io.StringIO())
            self.assertEqual([r["status"] for r in rows], ["failed"] + ["not_started"] * 4)
            self.assertEqual(json.loads(out.read_bytes())["status"], "error")
            self.assertNotIn("private failure", events.read_text())

    def test_incomplete_output_write_is_not_retried(self):
        cases, _, _, _ = check.sim.load_inputs(check.ROOT / "04-evals/examples/security_cases.jsonl",
                                               check.ROOT / "04-evals/examples/security_outputs.jsonl")
        records = [{"id":c["id"], "status":"not_started", "stage":None, "error":None,
                    "capture_started":False, "output_saved":False, "api_cleanup":"not_created",
                    "attack_source_exposed":None} for c in cases]
        def work(case, dbname, params, record, output_path, trace_stream):
            record["api_cleanup"] = "confirmed"
            record["output_write_started"] = True
            raise OSError("partial output")
        with tempfile.TemporaryDirectory() as temp:
            out, events = Path(temp) / "outputs", Path(temp) / "events"
            out.write_bytes(b"partial"); events.write_bytes(b"")
            with patch.object(check, "run_case", side_effect=work):
                rows = check.execute(cases, records, {}, out, events, io.StringIO())
            self.assertEqual(rows[0]["error"], "output_write_unconfirmed")
            self.assertEqual(out.read_bytes(), b"partial")
            self.assertEqual(rows[1]["status"], "not_started")


if __name__ == "__main__":
    unittest.main()
