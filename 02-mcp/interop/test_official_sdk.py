"""Interoperabilidad opcional con el cliente oficial MCP Python SDK 2.3.0."""
import json
import os
import sys
import unittest
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_rag import ANSWER, api


class OfficialSDKTests(unittest.TestCase):
    def test_rag_call_over_stdio(self):
        async def exercise():
            with api() as state:
                params = StdioServerParameters(
                    command=sys.executable, args=["-m", "mcp_lab.server"], cwd=ROOT,
                    env={"PYTHONPATH": str(ROOT / "src"),
                         "MCP_RAG_BASE_URL": os.environ["MCP_RAG_BASE_URL"],
                         "MCP_RAG_READ_TOKEN": os.environ["MCP_RAG_READ_TOKEN"]},
                )
                with anyio.fail_after(15):
                    async with Client(params, mode="legacy", read_timeout_seconds=5) as client:
                        response = await client.call_tool("ask_rag", {"question": "¿Qué contiene?"})
                        self.assertFalse(response.is_error)
                        self.assertEqual(json.loads(response.content[0].text), ANSWER)
                        invalid = await client.call_tool("ask_rag", {"question": " ", "limit": 3})
                        self.assertTrue(invalid.is_error)
                self.assertEqual(len(state["requests"]), 1)
        anyio.run(exercise)

    def check_client(self, mode):
        async def exercise():
            params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_lab.server"],
                                           env={"PYTHONPATH": str(ROOT / "src")}, cwd=ROOT)
            with anyio.fail_after(15):
                async with Client(params, mode=mode, read_timeout_seconds=5) as client:
                    self.assertEqual(client.protocol_version, "2025-11-25")
                    self.assertEqual(client.server_info.name, "ai-engineering-lab")
                    self.assertIsNotNone(client.server_capabilities.tools)
                    tools = await client.list_tools()
                    self.assertEqual([tool.name for tool in tools.tools], ["text_stats", "ask_rag"])
                    self.assertEqual(tools.tools[0].input_schema["properties"]["text"]["maxLength"], 4000)
                    answer = await client.call_tool("text_stats", {"text": "á😀 hola"})
                    self.assertFalse(answer.is_error)
                    self.assertEqual(json.loads(answer.content[0].text), {"characters": 7, "utf8_bytes": 11, "words": 2})
                    invalid = await client.call_tool("text_stats", {"text": 1})
                    self.assertTrue(invalid.is_error)
                    too_long = await client.call_tool("text_stats", {"text": "x" * 4001})
                    self.assertTrue(too_long.is_error)
                    with self.assertRaises(MCPError) as error:
                        await client.call_tool("no_existe", {})
                    self.assertEqual(error.exception.code, -32602)
                    empty = await client.call_tool("text_stats", {"text": ""})
                    self.assertEqual(json.loads(empty.content[0].text)["words"], 0)
        anyio.run(exercise)

    def test_legacy_handshake(self):
        self.check_client("legacy")

    def test_automatic_discovery_fallback(self):
        self.check_client("auto")

    def test_modern_version_not_supported(self):
        async def exercise():
            params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_lab.server"],
                                           env={"PYTHONPATH": str(ROOT / "src")}, cwd=ROOT)
            with anyio.fail_after(15):
                async with Client(params, mode="2026-07-28", read_timeout_seconds=5) as client:
                    with self.assertRaises(MCPError) as error:
                        await client.list_tools()
                    self.assertEqual(error.exception.code, -32002)
        anyio.run(exercise)

    def test_tool_budget_is_per_server_process(self):
        async def exercise():
            params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_lab.server"],
                                           env={"PYTHONPATH": str(ROOT / "src")}, cwd=ROOT)
            with anyio.fail_after(20):
                async with Client(params, mode="legacy", read_timeout_seconds=5) as client:
                    for _ in range(100):
                        self.assertFalse((await client.call_tool("text_stats", {"text": "x"})).is_error)
                    self.assertTrue((await client.call_tool("text_stats", {"text": "x"})).is_error)
                async with Client(params, mode="legacy", read_timeout_seconds=5) as client:
                    self.assertFalse((await client.call_tool("text_stats", {"text": "x"})).is_error)
        anyio.run(exercise)
