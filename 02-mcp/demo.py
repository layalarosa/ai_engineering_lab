"""Cliente de demostración: inicia el servidor e intercambia mensajes stdio."""
import json
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Cliente MCP didáctico por stdio")
    parser.add_argument("--ask", help="Pregunta opcional para la herramienta ask_rag")
    parser.add_argument("--limit", type=int, default=3, help="Número máximo de fuentes para RAG")
    args = parser.parse_args()
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "demo", "version": "0.1.0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "text_stats", "arguments": {"text": "Hola mundo"}}},
    ]
    if args.ask is not None:
        messages.append({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "ask_rag", "arguments": {"question": args.ask, "limit": args.limit}}})
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    payload = "".join(json.dumps(message, ensure_ascii=True) + "\n" for message in messages).encode("utf-8")
    # El cliente deja margen al máximo configurable de 600 segundos de la herramienta.
    completed = subprocess.run([sys.executable, "-m", "mcp_lab.server"], input=payload, capture_output=True, env=env, timeout=630 if args.ask is not None else 10, check=True)
    responses = [json.loads(line) for line in completed.stdout.decode("utf-8").splitlines()]
    expected_ids = [1, 2, 3, 4] if args.ask is not None else [1, 2, 3]
    if [item.get("id") for item in responses] != expected_ids or any("error" in item for item in responses):
        raise RuntimeError("El servidor no completó el intercambio esperado")
    print("Versión:", responses[0]["result"]["protocolVersion"])
    print("Herramientas:", ", ".join(tool["name"] for tool in responses[1]["result"]["tools"]))
    print("Resultado:", responses[2]["result"]["content"][0]["text"])
    if args.ask is not None:
        rag = responses[3]["result"]
        print("RAG:", rag["content"][0]["text"])
        if rag.get("isError"):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
