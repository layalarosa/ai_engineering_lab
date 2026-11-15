import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mcp_lab.server import MAX_LINE_BYTES


class StdioTests(unittest.TestCase):
    def run_server(self, data):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run([sys.executable, "-m", "mcp_lab.server"], input=data, capture_output=True, env=env, timeout=10)

    def test_full_exchange_and_clean_stdout(self):
        messages = [
            {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "client", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": "list", "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "text_stats", "arguments": {"text": "á😀"}}},
        ]
        data = b"".join((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8") for msg in messages)
        completed = self.run_server(data)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, b"")
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([r["id"] for r in responses], [0, "list", 2])
        self.assertEqual(json.loads(responses[2]["result"]["content"][0]["text"]), {"characters": 2, "utf8_bytes": 6, "words": 1})

    def test_malformed_messages_are_recoverable(self):
        completed = self.run_server(b'{oops}\n{"jsonrpc":"2.0","jsonrpc":"2.0"}\n\xff\n[]\n')
        self.assertEqual(completed.returncode, 0)
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([r["error"]["code"] for r in responses], [-32700, -32700, -32700, -32600])
        self.assertNotIn(b"oops", completed.stderr)

    def test_oversized_and_incomplete_frames_close_session(self):
        for data in (b"a" * (MAX_LINE_BYTES + 1) + b"\n", b'{"jsonrpc":"2.0"}'):
            with self.subTest(size=len(data)):
                completed = self.run_server(data)
                self.assertEqual(completed.returncode, 1)
                self.assertEqual(completed.stdout, b"")
                self.assertNotIn(data, completed.stderr)

    def test_demo_client(self):
        completed = subprocess.run([sys.executable, str(ROOT / "demo.py")], capture_output=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(b"text_stats", completed.stdout)
