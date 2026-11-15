"""Verifica por stdio el paquete Python o la imagen Docker ya construidos."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--zipapp", type=Path)
    target.add_argument("--image")
    args = parser.parse_args()
    command = ([sys.executable, str(args.zipapp.resolve())] if args.zipapp
               else ["docker", "run", "--rm", "-i", args.image])
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "text_stats", "arguments": {"text": "hola mundo"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "ask_rag", "arguments": {"question": "hola"}}},
    ]
    payload = b"".join((json.dumps(item) + "\n").encode("utf-8") for item in messages)
    env = os.environ.copy()
    for key in ("PYTHONPATH", "MCP_RAG_BASE_URL", "MCP_RAG_READ_TOKEN", "MCP_RAG_READ_TOKEN_FILE"):
        env.pop(key, None)
    with tempfile.TemporaryDirectory() as directory:
        completed = subprocess.run(command, input=payload, capture_output=True, cwd=directory, env=env, timeout=60)
        if completed.returncode != 0 or completed.stderr:
            raise RuntimeError("El servidor empaquetado terminó con error o escribió en stderr")
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        require([item["id"] for item in responses] == [1, 2, 3, 4], "Respuestas MCP inesperadas")
        require([tool["name"] for tool in responses[1]["result"]["tools"]] == ["text_stats", "ask_rag"], "Herramientas inesperadas")
        require(not responses[2]["result"]["isError"] and json.loads(responses[2]["result"]["content"][0]["text"]) == {"characters": 10, "utf8_bytes": 10, "words": 2}, "Estadísticas inesperadas")
        require(responses[3]["result"]["isError"] is True, "Se esperaba un error de configuración RAG")
        # También debe preservar el código de salida del servidor cuando hay una línea incompleta.
        incomplete = subprocess.run(command, input=b"{}", capture_output=True, cwd=directory, env=env, timeout=60)
        require(incomplete.returncode == 1 and not incomplete.stdout, "Salida incorrecta para mensaje incompleto")
    print("Verificación del artefacto completada correctamente.")


if __name__ == "__main__":
    main()
