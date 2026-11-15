"""Subconjunto educativo de JSON-RPC y MCP 2025-11-25."""

import json

from .tools import TOOL_DEFINITION, call_text_stats
from .rag_client import TOOL_DEFINITION as RAG_TOOL_DEFINITION, MAX_RESPONSE_BYTES, call_ask_rag, tool_error

PROTOCOL_VERSION = "2025-11-25"
MAX_REQUESTS = 10000
MAX_TOOL_CALLS = 100


def error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def result(request_id, value):
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


class Session:
    def __init__(self):
        self.state = "new"
        self.seen_ids = set()
        self.tool_calls = 0

    def handle(self, message):
        if not isinstance(message, dict):
            return error(None, -32600, "Invalid Request")
        has_id = "id" in message
        request_id = message.get("id")
        id_valid = ((isinstance(request_id, str) and len(request_id) <= 256) or
                    (type(request_id) is int and -(2**63) <= request_id < 2**63))
        if (message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str)
                or not message["method"] or (has_id and not id_valid)):
            return error(request_id if has_id and id_valid else None, -32600, "Invalid Request")
        method = message["method"]
        if not has_id:
            if method == "notifications/initialized" and self.state == "initializing" and isinstance(message.get("params", {}), dict):
                self.state = "ready"
            return None
        # Un cliente MCP no debe reutilizar identificadores dentro de una sesión.
        if request_id in self.seen_ids:
            return error(request_id, -32600, "Request ID already used")
        if len(self.seen_ids) >= MAX_REQUESTS:
            return error(request_id, -32600, "Session request limit reached")
        self.seen_ids.add(request_id)
        params = message.get("params", {})
        if not isinstance(params, dict):
            return error(request_id, -32602, "Invalid params")
        if method == "initialize":
            info = params.get("clientInfo")
            if (self.state != "new" or not isinstance(params.get("protocolVersion"), str)
                    or not params["protocolVersion"] or not isinstance(params.get("capabilities"), dict)
                    or not isinstance(info, dict) or not isinstance(info.get("name"), str)
                    or not info["name"] or not isinstance(info.get("version"), str) or not info["version"]):
                return error(request_id, -32602, "Invalid initialization")
            self.state = "initializing"
            return result(request_id, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}},
                                       "serverInfo": {"name": "ai-engineering-lab", "version": "0.1.0"}})
        if method == "ping" and self.state != "new":
            return result(request_id, {})
        if self.state != "ready":
            return error(request_id, -32002, "Server not initialized")
        if method == "tools/list":
            if "cursor" in params:
                return error(request_id, -32602, "Invalid cursor")
            return result(request_id, {"tools": [TOOL_DEFINITION, RAG_TOOL_DEFINITION]})
        if method == "tools/call":
            name, arguments = params.get("name"), params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return error(request_id, -32602, "Invalid params")
            if name not in ("text_stats", "ask_rag"):
                return error(request_id, -32602, "Unknown tool")
            if self.tool_calls >= MAX_TOOL_CALLS:
                return result(request_id, {"content": [{"type": "text", "text": "Session tool call limit reached"}], "isError": True})
            self.tool_calls += 1
            value = call_text_stats(arguments) if name == "text_stats" else call_ask_rag(arguments)
            response = result(request_id, value)
            # El límite incluye la envoltura y los escapes de la respuesta MCP.
            encoded = json.dumps(response, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            if len(encoded) + 1 > MAX_RESPONSE_BYTES:
                return result(request_id, tool_error("La respuesta MCP supera el tamaño permitido."))
            return response
        return error(request_id, -32601, "Method not found")
