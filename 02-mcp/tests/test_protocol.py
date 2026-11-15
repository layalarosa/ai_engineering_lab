import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mcp_lab.protocol import MAX_TOOL_CALLS, PROTOCOL_VERSION, Session


def request(method, request_id, params=None):
    value = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        value["params"] = params
    return value


def initialize(session):
    params = {"protocolVersion": "other-version", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}
    return session.handle(request("initialize", 0, params))


class ProtocolTests(unittest.TestCase):
    def test_initialization_discovery_call_and_ids(self):
        session = Session()
        self.assertEqual(session.handle(request("tools/list", 9))["error"]["code"], -32002)
        self.assertEqual(initialize(session)["result"]["protocolVersion"], PROTOCOL_VERSION)
        self.assertEqual(session.handle(request("ping", "ping"))["result"], {})
        self.assertEqual(session.handle(request("tools/list", 10))["error"]["code"], -32002)
        self.assertIsNone(session.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(session.handle(request("tools/list", "list"))["result"]["tools"][0]["name"], "text_stats")
        response = session.handle(request("tools/call", 1, {"name": "text_stats", "arguments": {"text": "Hola mundo"}}))
        self.assertEqual(json.loads(response["result"]["content"][0]["text"]), {"characters": 10, "utf8_bytes": 10, "words": 2})
        self.assertEqual(session.handle(request("tools/list", 1))["error"]["code"], -32600)

    def test_invalid_protocol_and_tool_errors(self):
        session = Session()
        self.assertEqual(session.handle(request("initialize", True))["error"]["code"], -32600)
        self.assertEqual(session.handle(request("initialize", None))["error"]["code"], -32600)
        self.assertEqual(session.handle(request("initialize", "x" * 257))["error"]["code"], -32600)
        self.assertEqual(session.handle(request("initialize", 2**63))["error"]["code"], -32600)
        self.assertEqual(session.handle(request("initialize", 3, {}))["error"]["code"], -32602)
        initialize(session)
        session.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(session.handle(request("unknown", 4))["error"]["code"], -32601)
        self.assertEqual(session.handle(request("tools/call", 5, {"name": "bad"}))["error"]["code"], -32602)
        self.assertTrue(session.handle(request("tools/call", 6, {"name": "text_stats", "arguments": {"text": 5}}))["result"]["isError"])
        self.assertIsNone(session.handle({"jsonrpc": "2.0", "method": "notifications/unknown"}))

    def test_tool_call_budget(self):
        session = Session()
        initialize(session)
        session.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        for i in range(1, MAX_TOOL_CALLS + 1):
            self.assertFalse(session.handle(request("tools/call", i, {"name": "text_stats", "arguments": {"text": "a"}}))["result"]["isError"])
        self.assertTrue(session.handle(request("tools/call", MAX_TOOL_CALLS + 1, {"name": "text_stats", "arguments": {"text": "a"}}))["result"]["isError"])
