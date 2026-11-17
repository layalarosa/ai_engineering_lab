"""Prueba manual de cinco corpus aislados con API RAG real y modelos simulados."""
import argparse
import hashlib
import json
import os
import secrets
import sys
import threading
import time
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import postgres_isolation_check as pg
import rag_api_postgres_fake_model_check as one
from evals_lab import security_orchestration_sim as sim
from evals_lab.security_capture import _encode, capture
from rag import answer, api, ollama, openai_client
from rag.observability import Trace
from rag.store import initialize

ROOT = pg.ROOT


def generation_for(case, documents, fixture, received):
    """El generador ficticio valida el contexto y devuelve texto predeterminado."""
    def generate(prompt, *, model, system, base_url):
        payload = json.loads(prompt)
        if (model != "fake-generation" or base_url != one.URL or system != answer.SYSTEM or
                not isinstance(payload, dict) or set(payload) != {"pregunta", "fragmentos"} or
                payload["pregunta"] != case["input"] or not isinstance(payload["fragmentos"], list)):
            raise ValueError("unexpected generation request")
        fragments = payload["fragmentos"]
        if (len(fragments) != len(documents) or
                any(not isinstance(row, dict) or set(row) != {"etiqueta", "texto"} or
                    row["etiqueta"] != f"[{i}]" or not isinstance(row["texto"], str)
                    for i, row in enumerate(fragments, 1)) or
                Counter(row["texto"] for row in fragments) != Counter(documents.values())):
            raise ValueError("unexpected generation context")
        received.append(fragments)
        return fixture["answer"]
    return generate


def verify_rows(conn, documents):
    rows = conn.execute("SELECT model, source, chunk_index, content FROM rag_chunks ORDER BY source").fetchall()
    models = conn.execute("SELECT model, dimensions FROM rag_models").fetchall()
    expected = sorted((one.MODEL, "api:" + doc_id, 0, text) for doc_id, text in documents.items())
    if rows != expected or models != [(one.MODEL, 2)]:
        raise ValueError("unexpected stored corpus")
    return len(rows)


def exercise_case(case, fixture, dbname, params, record):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    documents = sim.corpus_for(case)
    record["stage"] = "database_create"
    with psycopg.connect(dbname="postgres", **params) as admin:
        existing = {row[0] for row in admin.execute("SELECT datname FROM pg_database")}
        if dbname in existing:
            raise ValueError("database already exists")
        admin.execute(sql.SQL("CREATE DATABASE {} ").format(sql.Identifier(dbname)))
    dsn = make_conninfo(dbname=dbname, **{key: value for key, value in params.items() if key != "autocommit"})
    record["stage"] = "database_initialize"
    with psycopg.connect(dsn, autocommit=True) as conn:
        initialize(conn)
        if conn.execute("SELECT count(*) FROM rag_chunks").fetchone()[0] or conn.execute("SELECT count(*) FROM rag_models").fetchone()[0]:
            raise ValueError("database not empty")
        record["pgvector_version"] = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]
    write_token, read_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    settings = api.Settings(dsn, write_token, read_token, "ollama", one.MODEL, "ollama", "fake-generation", one.URL, None)
    server = thread = None
    received = []
    with ExitStack() as stack:
        embeddings = stack.enter_context(patch.object(ollama, "embed", side_effect=one.fake_embedding))
        generation = stack.enter_context(patch.object(answer, "generate", side_effect=generation_for(case, documents, fixture, received)))
        try:
            record["stage"] = "api_start"
            server = one.TestServer(("127.0.0.1", 0), settings)
            record["api_cleanup"] = "unconfirmed"
            thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
            thread.start()
            record["stage"] = "api_ingestion"
            for doc_id, text in documents.items():
                one.put_document(server.server_port, write_token, doc_id, text)
            record["documents_indexed"] = len(documents)
            record["stage"] = "database_verify"
            with psycopg.connect(dsn, autocommit=True) as conn:
                record["rows_verified"] = verify_rows(conn, documents)
            record["stage"] = "api_answer_capture"
            output, event = capture(case, token=read_token, port=server.server_port, limit=2, timeout_s=10)
            record["capture_outcome"] = event["outcome"]
            expected_sources = {(doc_id, 0, text) for doc_id, text in documents.items()}
            actual_sources = {(row["document_id"], row["chunk_index"], row["text"]) for row in output["sources"] or []}
            expected_context = [{"etiqueta": f"[{i}]", "texto": row["text"]}
                                for i, row in enumerate(output["sources"] or [], 1)]
            if (event["outcome"] != "ok_answered" or output["answer"] != fixture["answer"] or
                    actual_sources != expected_sources or len(output["sources"]) != len(expected_sources) or
                    received != [expected_context] or embeddings.call_count != len(documents) + 1 or generation.call_count != 1):
                raise ValueError("unexpected answer, sources or provider calls")
            record["context_and_sources_verified"] = True
            record["fake_embedding_calls"] = embeddings.call_count
            record["fake_generation_calls"] = generation.call_count
            record["output_line_sha256"] = hashlib.sha256(_encode(output)).hexdigest()
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
                    record["api_cleanup"] = "confirmed"
                except Exception:
                    record["api_cleanup"] = "unconfirmed"


def execute_cases(cases, fixtures, params):
    """Detiene al primer fallo; conserva cada etapa y los casos no iniciados."""
    records = [{"id": case["id"], "status": "not_started", "stage": None,
                "error": None, "api_cleanup": "not_created"} for case in cases]
    for index, (case, fixture, record) in enumerate(zip(cases, fixtures, records)):
        record["status"] = "started"
        try:
            exercise_case(case, fixture, pg.DB_NAMES[index], params, record)
            if record["api_cleanup"] != "confirmed":
                raise RuntimeError("API cleanup unconfirmed")
            record["status"], record["stage"] = "completed", "completed"
        except KeyboardInterrupt:
            record["status"], record["error"] = "failed", "interrupted"
        except Exception:
            record["status"], record["error"] = "failed", "stage_failed"
        if record["status"] == "failed":
            break
    return records


def run_check(cases, fixtures, cases_hash, fixtures_hash):
    import psycopg

    run_id = secrets.token_hex(16)
    name = "aielab-security-" + run_id
    password = secrets.token_urlsafe(32)
    env = dict(os.environ, POSTGRES_PASSWORD=password)
    records = [{"id": case["id"], "status": "not_started", "stage": None,
                "error": None, "api_cleanup": "not_created"} for case in cases]
    report = {"schema_version": 1, "mode": "five_real_rag_api_postgres_fake_providers",
              "started_utc": datetime.now(timezone.utc).isoformat(), "cases_sha256": cases_hash,
              "fixtures_sha256": fixtures_hash, "image": pg.IMAGE, "planned": len(cases),
              "status": "failed", "failure_stage": None, "container_cleanup": "unconfirmed",
              "image_verified": False, "temporary_storage_verified": False, "loopback_binding_verified": False,
              "external_provider_calls": None, "cross_database_check": "not_started", "cases": records}
    creation_started = creation_confirmed = False
    guards = []
    stage = "image_check"
    try:
        report["image_id"] = pg.docker("image", "inspect", pg.IMAGE, "--format", "{{.Id}}").stdout.decode("ascii").strip()
        paths = [Path(__file__).resolve(), Path(one.__file__).resolve(), Path(pg.__file__).resolve(),
                 ROOT / "01-rag/src/rag/api.py", ROOT / "01-rag/src/rag/answer.py", ROOT / "01-rag/src/rag/store.py",
                 ROOT / "01-rag/src/rag/ingestion.py", ROOT / "01-rag/src/rag/chunking.py", ROOT / "01-rag/src/rag/ollama.py",
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
                conn = psycopg.connect(dbname="postgres", **params)
                conn.close()
                break
            except psycopg.Error:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.5)
        for provider in (ollama, openai_client):
            guard = patch.object(provider, "urlopen", side_effect=AssertionError("model access blocked"))
            guards.append((guard, guard.start()))
        stage = "cases"
        with patch.object(api, "Trace", side_effect=lambda enabled: Trace(enabled=False)):
            records = execute_cases(cases, fixtures, params)
        report["cases"] = records
        if any(record["status"] != "completed" for record in records):
            raise ValueError("case failed")
        if any(mock.call_count for _, mock in guards):
            raise ValueError("unexpected provider call")
        report["external_provider_calls"] = 0
        stage = "cross_database_check"
        for case, dbname in zip(cases, pg.DB_NAMES):
            with psycopg.connect(dbname=dbname, **params) as conn:
                verify_rows(conn, sim.corpus_for(case))
        report["cross_database_check"] = "passed"
        report["status"] = "completed"
    except KeyboardInterrupt:
        report["failure_stage"] = "interrupted"
    except Exception:
        report["failure_stage"] = stage
    finally:
        if any(row["api_cleanup"] == "unconfirmed" for row in report["cases"]):
            report["provider_network_guards"] = "retained_until_process_exit"
        else:
            for guard, _ in reversed(guards):
                guard.stop()
        report["container_cleanup"] = pg.cleanup(name, run_id) if creation_started else "not_created"
        if creation_started and not creation_confirmed and report["container_cleanup"] == "confirmed_absent":
            report["container_cleanup"] = "unconfirmed"
        if report["container_cleanup"] not in ("confirmed_removed", "confirmed_absent", "not_created"):
            report["status"] = "failed"
            report["pending_container_name"] = name
            report["pending_container_label"] = pg.LABEL + "=" + run_id
        report["completed_utc"] = datetime.now(timezone.utc).isoformat()
        report["counts"] = {status: sum(row["status"] == status for row in report["cases"])
                            for status in ("completed", "failed", "not_started")}
    return report


def main():
    parser = argparse.ArgumentParser(description="Cinco casos RAG con proveedores simulados.")
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    try:
        cases, fixtures, cases_hash, fixtures_hash = sim.load_inputs(ROOT / "04-evals/examples/security_cases.jsonl",
                                                                     ROOT / "04-evals/examples/security_outputs.jsonl")
        report = run_check(cases, fixtures, cases_hash, fixtures_hash)
        print(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
        return 0 if report["status"] == "completed" else 1
    except Exception:
        print("No se pudo iniciar la integración simulada.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
