"""Prueba opcional MCP → API RAG → PostgreSQL y Ollama reales.

RAG_TEST_DATABASE_URL debe apuntar a una base de pruebas dedicada.
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
RAG_ROOT = ROOT.parent / "01-rag"


@unittest.skipUnless(os.environ.get("MCP_LIVE_TEST") == "1", "requiere MCP_LIVE_TEST=1 y servicios reales")
class LiveRagTests(unittest.TestCase):
    def test_full_path(self):
        dsn = os.environ["RAG_TEST_DATABASE_URL"]
        rag_python = os.environ["RAG_PYTHON"]
        embed_model = os.environ.get("RAG_LIVE_EMBED_MODEL", "nomic-embed-text")
        generation_model = os.environ.get("RAG_LIVE_GENERATION_MODEL", "llama3.1:8b")
        doc_id = "mcp_smoke_" + secrets.token_hex(12)
        write_token, read_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = os.environ.copy()
        env.update({"PYTHONPATH": str(RAG_ROOT / "src"), "DATABASE_URL": dsn,
                    "RAG_WRITE_TOKEN": write_token, "RAG_READ_TOKEN": read_token,
                    "RAG_EMBED_PROVIDER": "ollama", "RAG_GENERATION_PROVIDER": "ollama",
                    "RAG_EMBED_MODEL": embed_model, "RAG_GENERATION_MODEL": generation_model,
                    "OLLAMA_BASE_URL": os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
                    "RAG_PORT": str(port)})
        # Evitar que configuración ajena cambie la prueba o cause ambigüedad.
        for key in ("DATABASE_URL_FILE", "RAG_WRITE_TOKEN_FILE", "RAG_READ_TOKEN_FILE", "RAG_MIN_SCORE"):
            env.pop(key, None)
        subprocess.run([rag_python, "-m", "rag.database_cli", "init"], env=env, cwd=RAG_ROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30)
        server = subprocess.Popen([rag_python, "-m", "rag.api"], env=env, cwd=RAG_ROOT,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 20
            while True:
                if server.poll() is not None:
                    self.fail("La API RAG no pudo iniciarse.")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fail("La API RAG no respondió al iniciarse.")
                    time.sleep(0.1)

            import http.client
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=180)
            try:
                text = "El proyecto Faro identifica sus informes con el color violeta."
                connection.request("PUT", "/v1/documents/" + doc_id, body=text.encode("utf-8"),
                                   headers={"Authorization": "Bearer " + write_token, "Content-Type": "text/plain; charset=utf-8"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200, "Falló la indexación HTTP del documento de prueba")
                self.assertEqual(json.loads(response.read())["document_id"], doc_id)
            finally:
                connection.close()

            async def exercise():
                params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_lab.server"], cwd=ROOT,
                    env={"PYTHONPATH": str(ROOT / "src"), "MCP_RAG_BASE_URL": f"http://127.0.0.1:{port}",
                         "MCP_RAG_READ_TOKEN": read_token})
                with anyio.fail_after(300):
                    async with Client(params, mode="legacy", read_timeout_seconds=290) as client:
                        result = await client.call_tool("ask_rag", {"question": "¿Qué color identifica los informes del proyecto Faro?", "limit": 1})
                        self.assertFalse(result.is_error, result.content[0].text)
                        answer = json.loads(result.content[0].text)
                        self.assertEqual(answer["status"], "answered")
                        self.assertTrue(answer["answer"].strip())
                        self.assertEqual(len(answer["sources"]), 1)
                        self.assertEqual(answer["sources"][0]["document_id"], doc_id)
                        self.assertIn("violeta", answer["sources"][0]["text"])
                        print("Respuesta del modelo:", answer["answer"])
                        print("Fuente recuperada: documento temporal, fragmento", answer["sources"][0]["chunk_index"])
            anyio.run(exercise)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
            cleanup = ("import os,psycopg,sys; "
                       "c=psycopg.connect(os.environ['DATABASE_URL'],autocommit=True); "
                       "c.execute('DELETE FROM rag_chunks WHERE model = %s AND source = %s', "
                       "(sys.argv[1], 'api:' + sys.argv[2])); c.close()")
            subprocess.run([rag_python, "-c", cleanup, embed_model, doc_id], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30)
