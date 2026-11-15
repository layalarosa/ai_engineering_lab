"""Comprueba permisos y contenido usando exclusivamente credenciales de prueba."""
import argparse
import http.client
import json
import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from rag.ingestion import DocumentChunk
from rag.api import Server, Settings
from rag.store import replace_document, search


def check(mode, dsn):
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        if mode == "source":
            replace_document(conn, "test-model", "api:doc", [DocumentChunk("api:doc", 0, "documento sintético")], [[1.0, 0.0]])
        results = search(conn, "test-model", [1.0, 0.0], source_prefix="api:")
        if len(results) != 1 or results[0].chunk.text != "documento sintético" or abs(results[0].score - 1) > 1e-6:
            raise AssertionError("el documento o su vector no coinciden")
        for query in ("CREATE TABLE public.forbidden_table (id int)",
                      "CREATE SCHEMA forbidden_schema",
                      "DROP TABLE public.rag_chunks", "TRUNCATE TABLE public.rag_chunks",
                      "UPDATE public.rag_models SET dimensions = 3 WHERE model = 'test-model'",
                      "DELETE FROM public.rag_models WHERE model = 'test-model'"):
            try:
                conn.execute(query)
            except psycopg.errors.InsufficientPrivilege:
                pass
            else:
                raise AssertionError("se concedió un permiso no previsto")
        replace_document(conn, "test-model", "api:doc", [DocumentChunk("api:doc", 0, "documento sintético")], [[1.0, 0.0]])
        settings = Settings(dsn, "w" * 32, "r" * 32, "ollama", "test-model", "ollama", "test", "http://localhost:11434", None)
        server = Server(("127.0.0.1", 0), settings)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch("rag.api.ollama.embed", return_value=[[1.0, 0.0]]), patch("rag.answer.generate", return_value="respuesta de prueba"):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
                try:
                    connection.request("POST", "/v1/answers", b'{"question":"q"}',
                                 {"Authorization": "Bearer " + "r" * 32, "Content-Type": "application/json"})
                    response = connection.getresponse()
                    data = json.loads(response.read())
                    if response.status != 200 or not any(s["document_id"] == "doc" for s in data.get("sources", [])):
                        raise AssertionError("la API no pudo consultar con el rol restringido")
                finally:
                    connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    print(f"{mode}: lectura, indexación y restricciones verificadas.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["source", "restored"])
    args = parser.parse_args()
    dsn = os.environ["RAG_FIXTURE_RUNTIME_SOURCE" if args.mode == "source" else "RAG_FIXTURE_RUNTIME_RESTORED"]
    check(args.mode, dsn)


if __name__ == "__main__":
    main()
