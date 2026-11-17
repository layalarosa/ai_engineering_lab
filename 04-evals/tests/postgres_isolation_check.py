"""Verificación manual: PostgreSQL efímero y respuestas de modelo simuladas.

Ejecutar con el entorno Python de 01-rag. No se incluye en unittest/CI por
requerir Docker local; nunca descarga imágenes ni consulta proveedores.
"""
import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "01-rag" / "src"))
sys.path.insert(0, str(ROOT / "04-evals" / "src"))

from evals_lab import security_orchestration_sim as sim
from evals_lab.security_capture import capture
from rag.ingestion import ingest_text
from rag.store import initialize, replace_document, search

IMAGE = "pgvector/pgvector:0.8.0-pg16"
LABEL = "ai-engineering-lab.security-isolation"
MODEL = "synthetic-vectors-v1"
DB_NAMES = tuple(f"security_case_{i}" for i in range(5))


def docker(*args, env=None, timeout=30, check=True):
    result = subprocess.run(["docker", *args], env=env, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError("docker operation failed")
    return result


def inspect_owned(name, run_id):
    result = docker("inspect", name, check=False)
    if result.returncode:
        raise RuntimeError("container ownership could not be verified")
    data = json.loads(result.stdout)
    if not isinstance(data, list) or len(data) != 1:
        raise RuntimeError("invalid inspect response")
    container = data[0]
    if (container.get("Name") != "/" + name or
            container.get("Config", {}).get("Labels", {}).get(LABEL) != run_id):
        raise RuntimeError("container ownership mismatch")
    identifier = container.get("Id")
    if not isinstance(identifier, str) or len(identifier) != 64 or any(c not in "0123456789abcdef" for c in identifier):
        raise RuntimeError("invalid container identifier")
    return container


def cleanup(name, run_id):
    """Solo borra el contenedor que coincide con nombre, etiqueta e ID verificado."""
    try:
        listing = docker("ps", "-a", "--no-trunc", "--filter", "label=" + LABEL + "=" + run_id,
                         "--format", "{{.ID}}")
        ids = listing.stdout.decode("ascii").splitlines()
        if not ids:
            return "confirmed_absent"
        if len(ids) != 1:
            return "unconfirmed"
        container = inspect_owned(name, run_id)
        if ids[0] != container["Id"]:
            return "unconfirmed"
        docker("rm", "-f", "-v", container["Id"], timeout=45)
        remaining = docker("ps", "-a", "--no-trunc", "--filter", "label=" + LABEL + "=" + run_id,
                           "--format", "{{.ID}}")
        return "confirmed_removed" if not remaining.stdout.strip() else "unconfirmed"
    except Exception:
        return "unconfirmed"


def run_check(cases, fixtures, cases_hash, fixtures_hash):
    import psycopg
    from psycopg import sql

    run_id = secrets.token_hex(16)
    name = "aielab-security-" + run_id
    password = secrets.token_urlsafe(32)
    env = dict(os.environ, POSTGRES_PASSWORD=password)
    records = [{"id": c["id"], "status": "not_started", "rows_verified": None,
                "retrieval_verified": None, "simulated_capture": None} for c in cases]
    report = {"schema_version": 1, "mode": "postgres_pgvector_synthetic_model_simulated",
              "started_utc": datetime.now(timezone.utc).isoformat(), "cases_sha256": cases_hash,
              "fixtures_sha256": fixtures_hash, "image": IMAGE, "image_id": None,
              "planned": len(cases), "status": "failed", "failure_stage": None,
              "container_cleanup": "unconfirmed", "container_image_verified": False,
              "temporary_storage_verified": False, "loopback_binding_verified": False,
              "python_version": sys.version.split()[0], "cases": records}
    stage = "image_check"
    creation_started = False
    creation_confirmed = False
    try:
        image = docker("image", "inspect", IMAGE, "--format", "{{.Id}}")
        report["image_id"] = image.stdout.decode("ascii").strip()
        paths = [Path(__file__).resolve(), ROOT / "01-rag/src/rag/store.py", ROOT / "01-rag/src/rag/ingestion.py",
                 ROOT / "01-rag/src/rag/chunking.py", ROOT / "01-rag/src/rag/similarity.py",
                 ROOT / "04-evals/src/evals_lab/security_capture.py", ROOT / "04-evals/src/evals_lab/security_orchestration_sim.py"]
        report["source_sha256"] = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        stage = "container_create"
        creation_started = True
        docker("run", "--detach", "--rm", "--pull", "never", "--name", name,
               "--label", LABEL + "=" + run_id, "--env", "POSTGRES_PASSWORD",
               "--tmpfs", "/var/lib/postgresql/data:rw,size=256m", "--publish", "127.0.0.1::5432", IMAGE,
               env=env, timeout=60)
        creation_confirmed = True
        container = inspect_owned(name, run_id)
        if container.get("Image") != report["image_id"]:
            raise RuntimeError("container image changed")
        report["container_image_verified"] = True
        if container.get("Mounts") or "/var/lib/postgresql/data" not in (container.get("HostConfig", {}).get("Tmpfs") or {}):
            raise RuntimeError("unexpected container storage")
        report["temporary_storage_verified"] = True
        binding = container["NetworkSettings"]["Ports"]["5432/tcp"]
        if len(binding) != 1 or binding[0]["HostIp"] != "127.0.0.1":
            raise RuntimeError("unexpected network binding")
        report["loopback_binding_verified"] = True
        port = int(binding[0]["HostPort"])
        params = {"host": "127.0.0.1", "port": port, "user": "postgres", "password": password,
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
            stage = "create_databases"
            existing = {row[0] for row in admin.execute("SELECT datname FROM pg_database")}
            if any(dbname in existing for dbname in DB_NAMES):
                raise RuntimeError("database already exists")
            for dbname in DB_NAMES:
                admin.execute(sql.SQL("CREATE DATABASE {} ").format(sql.Identifier(dbname)))
        finally:
            admin.close()

        stage = "initialize_and_index"
        for dbname, case, record in zip(DB_NAMES, cases, records):
            record["status"] = "started"
            documents = sim.corpus_for(case)
            with psycopg.connect(dbname=dbname, **params) as conn:
                initialize(conn)
                if conn.execute("SELECT count(*) FROM rag_chunks").fetchone()[0] != 0 or conn.execute("SELECT count(*) FROM rag_models").fetchone()[0] != 0:
                    raise RuntimeError("database is not empty")
                for index, (doc_id, text) in enumerate(documents.items()):
                    chunks = ingest_text(text, "api:" + doc_id, 500, 50)
                    if len(chunks) != 1 or chunks[0].text != text:
                        raise RuntimeError("unexpected chunks")
                    replace_document(conn, MODEL, "api:" + doc_id, chunks, [[1.0, 0.25 + index]])

        stage = "verify_and_simulate"
        for dbname, case, fixture, record in zip(DB_NAMES, cases, fixtures, records):
            documents = sim.corpus_for(case)
            with psycopg.connect(dbname=dbname, **params) as conn:
                rows = conn.execute("SELECT model, source, chunk_index, content FROM rag_chunks ORDER BY source").fetchall()
                record["pgvector_version"] = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]
                expected = sorted((MODEL, "api:" + doc_id, 0, text) for doc_id, text in documents.items())
                models = conn.execute("SELECT model, dimensions FROM rag_models").fetchall()
                if rows != expected or models != [(MODEL, 2)]:
                    raise RuntimeError("unexpected database rows")
                record["rows_verified"] = len(rows)
                found = search(conn, MODEL, [1.0, .25], limit=2, source_prefix="api:")
                actual = {(item.chunk.source[4:], item.chunk.index, item.chunk.text) for item in found}
                expected_sources = {(doc_id, 0, text) for doc_id, text in documents.items()}
                if actual != expected_sources or len(found) != len(expected_sources):
                    raise RuntimeError("unexpected vector retrieval")
                record["retrieval_verified"] = len(found)
            token = secrets.token_urlsafe(32)
            server, thread = sim._start_server(case, fixture, token)
            try:
                output, event = capture(case, token=token, port=server.server_port, limit=2, timeout_s=5)
            finally:
                sim._stop_server(server, thread)
            if event["outcome"] not in ("ok_answered", "ok_no_context") or not sim._sources_match(output, documents):
                raise RuntimeError("simulated capture failed")
            record["simulated_capture"] = event["outcome"]
            record["status"] = "completed"
        report["status"] = "completed"
    except KeyboardInterrupt:
        report["failure_stage"] = "interrupted"
    except Exception:
        report["failure_stage"] = stage
    finally:
        report["container_cleanup"] = cleanup(name, run_id) if creation_started else "not_created"
        if creation_started and not creation_confirmed and report["container_cleanup"] == "confirmed_absent":
            report["container_cleanup"] = "unconfirmed"
        if report["container_cleanup"] not in ("confirmed_removed", "confirmed_absent", "not_created"):
            report["status"] = "failed"
            report["pending_container_name"] = name
            report["pending_container_label"] = LABEL + "=" + run_id
        report["completed_utc"] = datetime.now(timezone.utc).isoformat()
        report["counts"] = {state: sum(c["status"] == state for c in records)
                            for state in ("completed", "started", "not_started")}
    return report


def main():
    parser = argparse.ArgumentParser(description="Verifica PostgreSQL temporal con vectores sintéticos.")
    parser.add_argument("--run", action="store_true", required=True, help="Confirma la creación del contenedor temporal.")
    parser.parse_args()
    try:
        cases, fixtures, cases_hash, fixtures_hash = sim.load_inputs(ROOT / "04-evals/examples/security_cases.jsonl",
                                                                     ROOT / "04-evals/examples/security_outputs.jsonl")
        report = run_check(cases, fixtures, cases_hash, fixtures_hash)
        print(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
        return 0 if report["status"] == "completed" else 1
    except Exception:
        print("No se pudo iniciar la verificación PostgreSQL.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
