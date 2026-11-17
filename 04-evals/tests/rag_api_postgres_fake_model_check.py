"""Prueba manual de API RAG real + pgvector, con proveedores simulados."""
import argparse
import hashlib
import http.client
import json
import os
import secrets
import sys
import threading
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import postgres_isolation_check as pg
from evals_lab import security_orchestration_sim as sim
from evals_lab.security_capture import _encode, capture
from rag import answer, api, ollama, openai_client
from rag.observability import Trace
from rag.store import initialize

ROOT = pg.ROOT
DBNAME = "security_api_case"
MODEL = "synthetic-vectors-v1"
URL = "http://127.0.0.1:9"


class TestServer(api.Server):
    """Espera a los hilos de solicitudes antes de restaurar los proveedores."""
    daemon_threads = False
    block_on_close = True


def fake_embedding(texts, *, model, base_url):
    if model != MODEL or base_url != URL or not isinstance(texts, list) or not texts:
        raise ValueError("unexpected synthetic embedding request")
    return [[1.0, 0.25] for _ in texts]


def generation_for(case, fixture):
    def fake_generation(prompt, *, model, system, base_url):
        payload = json.loads(prompt)
        if (model != "fake-generation" or base_url != URL or system != answer.SYSTEM or
                payload != {"pregunta": case["input"], "fragmentos": [
                    {"etiqueta": "[1]", "texto": case["attack_source"]["text"]}]}):
            raise ValueError("unexpected generation context")
        return fixture["answer"]
    return fake_generation


def put_document(port, token, doc_id, text):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("PUT", "/v1/documents/" + doc_id, text.encode("utf-8"),
                     {"Authorization": "Bearer " + token, "Content-Type": "text/plain; charset=utf-8"})
        response = conn.getresponse()
        lengths = response.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit() or int(lengths[0]) > 32000:
            raise ValueError("invalid ingestion response")
        raw = response.read(int(lengths[0]))
        if response.status != 200 or json.loads(raw) != {"document_id": doc_id, "chunks_indexed": 1}:
            raise ValueError("ingestion failed")
    finally:
        conn.close()


def run_check(case, fixture, cases_hash, fixtures_hash):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    run_id = secrets.token_hex(16)
    name = "aielab-security-" + run_id
    password = secrets.token_urlsafe(32)
    write_token, read_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    env = dict(os.environ, POSTGRES_PASSWORD=password)
    report = {"schema_version": 1, "mode": "real_rag_api_postgres_fake_providers",
              "started_utc": datetime.now(timezone.utc).isoformat(), "case_id": case["id"],
              "cases_sha256": cases_hash, "fixtures_sha256": fixtures_hash, "image": pg.IMAGE,
              "status": "failed", "failure_stage": None, "container_cleanup": "unconfirmed",
              "api_cleanup": "not_created", "image_verified": False, "temporary_storage_verified": False,
              "loopback_binding_verified": False, "simulated_providers": True, "external_provider_calls": None}
    creation_started = creation_confirmed = False
    server = thread = None
    network_guards = []
    stage = "image_check"
    try:
        image = pg.docker("image", "inspect", pg.IMAGE, "--format", "{{.Id}}")
        report["image_id"] = image.stdout.decode("ascii").strip()
        paths = [Path(__file__).resolve(), Path(pg.__file__).resolve(), ROOT / "01-rag/src/rag/api.py",
                 ROOT / "01-rag/src/rag/answer.py", ROOT / "01-rag/src/rag/ollama.py", ROOT / "01-rag/src/rag/store.py",
                 ROOT / "01-rag/src/rag/ingestion.py", ROOT / "01-rag/src/rag/chunking.py",
                 ROOT / "04-evals/src/evals_lab/security_capture.py", ROOT / "04-evals/src/evals_lab/security_orchestration_sim.py"]
        report["source_sha256"] = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        stage = "container_create"
        creation_started = True
        pg.docker("run", "--detach", "--rm", "--pull", "never", "--name", name,
                  "--label", pg.LABEL + "=" + run_id, "--env", "POSTGRES_PASSWORD",
                  "--tmpfs", "/var/lib/postgresql/data:rw,size=256m", "--publish", "127.0.0.1::5432", pg.IMAGE,
                  env=env, timeout=60)
        creation_confirmed = True
        container = pg.inspect_owned(name, run_id)
        if container.get("Image") != report["image_id"]:
            raise ValueError("image mismatch")
        report["image_verified"] = True
        if container.get("Mounts") or "/var/lib/postgresql/data" not in (container.get("HostConfig", {}).get("Tmpfs") or {}):
            raise ValueError("unexpected storage")
        report["temporary_storage_verified"] = True
        binding = container["NetworkSettings"]["Ports"]["5432/tcp"]
        if len(binding) != 1 or binding[0]["HostIp"] != "127.0.0.1":
            raise ValueError("unexpected network binding")
        report["loopback_binding_verified"] = True
        params = {"host": "127.0.0.1", "port": int(binding[0]["HostPort"]), "user": "postgres", "password": password,
                  "connect_timeout": 2, "autocommit": True}
        stage = "postgres_ready"
        deadline = time.monotonic() + 60
        while True:
            try:
                admin = psycopg.connect(dbname="postgres", **params)
                break
            except psycopg.Error:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.5)
        try:
            existing = {row[0] for row in admin.execute("SELECT datname FROM pg_database")}
            if DBNAME in existing:
                raise ValueError("database already exists")
            admin.execute(sql.SQL("CREATE DATABASE {} ").format(sql.Identifier(DBNAME)))
        finally:
            admin.close()
        stage = "database_initialize"
        dsn = make_conninfo(dbname=DBNAME, **{key: value for key, value in params.items() if key != "autocommit"})
        with psycopg.connect(dsn, autocommit=True) as conn:
            initialize(conn)
            if conn.execute("SELECT count(*) FROM rag_chunks").fetchone()[0] != 0 or conn.execute("SELECT count(*) FROM rag_models").fetchone()[0] != 0:
                raise ValueError("database is not empty")
            report["pgvector_version"] = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]
        settings = api.Settings(dsn, write_token, read_token, "ollama", MODEL, "ollama", "fake-generation", URL, None)
        for provider in (ollama, openai_client):
            guard = patch.object(provider, "urlopen", side_effect=AssertionError("model access blocked"))
            network_guards.append((guard, guard.start()))
        with ExitStack() as stack:
            embeddings = stack.enter_context(patch.object(ollama, "embed", side_effect=fake_embedding))
            generation = stack.enter_context(patch.object(answer, "generate", side_effect=generation_for(case, fixture)))
            ollama_net, openai_net = (entry[1] for entry in network_guards)
            stack.enter_context(patch.object(api, "Trace", side_effect=lambda enabled: Trace(enabled=False)))
            try:
                stage = "api_start"
                server = TestServer(("127.0.0.1", 0), settings)
                report["api_cleanup"] = "unconfirmed"
                thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
                thread.start()
                stage = "api_ingestion"
                source = case["attack_source"]
                put_document(server.server_port, write_token, source["document_id"], source["text"])
                report["put_http"] = 200
                stage = "database_verify"
                with psycopg.connect(dsn, autocommit=True) as conn:
                    rows = conn.execute("SELECT model, source, chunk_index, content FROM rag_chunks ORDER BY source").fetchall()
                    models = conn.execute("SELECT model, dimensions FROM rag_models").fetchall()
                    if rows != [(MODEL, "api:" + source["document_id"], 0, source["text"])] or models != [(MODEL, 2)]:
                        raise ValueError("unexpected stored corpus")
                report["rows_verified"] = len(rows)
                stage = "api_answer_capture"
                output, event = capture(case, token=read_token, port=server.server_port, limit=2, timeout_s=10)
                report["capture_outcome"] = event["outcome"]
                if (event["outcome"] != "ok_answered" or output["answer"] != fixture["answer"] or
                        output["sources"] != [source]):
                    raise ValueError("unexpected answer or source")
                report["context_and_source_verified"] = True
                report["output_line_sha256"] = hashlib.sha256(_encode(output)).hexdigest()
                if embeddings.call_count != 2 or generation.call_count != 1 or ollama_net.call_count or openai_net.call_count:
                    raise ValueError("unexpected provider calls")
                report["fake_embedding_calls"] = embeddings.call_count
                report["fake_generation_calls"] = generation.call_count
                report["external_provider_calls"] = 0
                report["status"] = "completed"
            finally:
                if server is not None:
                    try:
                        if thread is not None and thread.is_alive():
                            server.shutdown()
                        server.server_close()
                        if thread is not None:
                            thread.join(timeout=10)
                            if thread.is_alive():
                                raise RuntimeError("server thread remains")
                        report["api_cleanup"] = "confirmed"
                    except Exception:
                        report["api_cleanup"] = "unconfirmed"
                        report["status"] = "failed"
    except KeyboardInterrupt:
        report["failure_stage"] = "interrupted"
    except Exception:
        report["failure_stage"] = stage
    finally:
        if report["api_cleanup"] != "unconfirmed":
            for guard, _ in reversed(network_guards):
                guard.stop()
        elif network_guards:
            report["provider_network_guards"] = "retained_until_process_exit"
        report["container_cleanup"] = pg.cleanup(name, run_id) if creation_started else "not_created"
        if creation_started and not creation_confirmed and report["container_cleanup"] == "confirmed_absent":
            report["container_cleanup"] = "unconfirmed"
        if report["container_cleanup"] not in ("confirmed_removed", "confirmed_absent", "not_created"):
            report["status"] = "failed"
            report["pending_container_name"] = name
            report["pending_container_label"] = pg.LABEL + "=" + run_id
        report["completed_utc"] = datetime.now(timezone.utc).isoformat()
    return report


def main():
    parser = argparse.ArgumentParser(description="Prueba la API RAG real con proveedores simulados.")
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    try:
        cases, fixtures, cases_hash, fixtures_hash = sim.load_inputs(ROOT / "04-evals/examples/security_cases.jsonl",
                                                                     ROOT / "04-evals/examples/security_outputs.jsonl")
        report = run_check(cases[3], fixtures[3], cases_hash, fixtures_hash)
        print(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
        return 0 if report["status"] == "completed" else 1
    except Exception:
        print("No se pudo iniciar la integración simulada.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
