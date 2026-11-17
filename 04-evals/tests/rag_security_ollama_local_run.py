"""Ejecuta cinco casos sintéticos con API RAG y Ollama local; requiere --run."""
import argparse
import hashlib
import io
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import postgres_isolation_check as pg
import rag_api_postgres_fake_model_check as local
from evals_lab import security_orchestration_sim as sim
from evals_lab.security_capture import _encode, _error, capture
from rag import api, ollama, openai_client
from rag.observability import Trace
from rag.store import initialize

ROOT = pg.ROOT
OLLAMA_URL = "http://127.0.0.1:11434"
EMBED_MODEL = "nomic-embed-text:latest"
GEN_MODEL = "llama3.1:8b"
EXPECTED_MODELS = (EMBED_MODEL, GEN_MODEL)


def local_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def checked_urlopen(opener, request, *, timeout):
    """Permite solo los dos endpoints de modelo en la dirección fija local."""
    url = request.full_url if isinstance(request, urllib.request.Request) else request
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != 11434 or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            parsed.path not in ("/api/embed", "/api/generate")):
        raise ValueError("destino de modelo no permitido")
    return opener.open(request, timeout=timeout)


def model_digests(opener):
    with opener.open(OLLAMA_URL + "/api/tags", timeout=10) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("inventario de modelos demasiado grande")
    payload = json.loads(raw)
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise ValueError("inventario inválido")
    result = {}
    for item in models:
        if not isinstance(item, dict) or item.get("name") not in EXPECTED_MODELS:
            continue
        name, digest = item["name"], item.get("digest")
        if (name in result or not isinstance(digest, str) or len(digest) != 64 or
                any(ch not in "0123456789abcdef" for ch in digest)):
            raise ValueError("identidad de modelo inválida")
        result[name] = digest
    if set(result) != set(EXPECTED_MODELS):
        raise ValueError("falta un modelo local")
    return result


def _write_new(path, data):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _append(path, data):
    with path.open("ab") as stream:
        if stream.write(data) != len(data):
            raise OSError("incomplete write")
        stream.flush()
        os.fsync(stream.fileno())


def put_document(port, token, doc_id, text):
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=180)
    try:
        conn.request("PUT", "/v1/documents/" + doc_id, text.encode("utf-8"),
                     {"Authorization": "Bearer " + token, "Content-Type": "text/plain; charset=utf-8"})
        response = conn.getresponse()
        lengths = response.headers.get_all("Content-Length", [])
        if (len(lengths) != 1 or response.headers.get_all("Transfer-Encoding", []) or
                not lengths[0].isdigit() or len(lengths[0]) > 10 or int(lengths[0]) > 32000):
            raise ValueError("respuesta de ingesta inválida")
        size = int(lengths[0])
        raw = response.read(size)
        if len(raw) != size or response.status != 200 or json.loads(raw) != {"document_id": doc_id, "chunks_indexed": 1}:
            raise ValueError("ingesta no confirmada")
    finally:
        conn.close()


def verify_rows(conn, model, documents):
    rows = conn.execute("SELECT model, source, chunk_index, content FROM rag_chunks ORDER BY source").fetchall()
    models = conn.execute("SELECT model, dimensions FROM rag_models").fetchall()
    expected = sorted((model, "api:" + doc_id, 0, text) for doc_id, text in documents.items())
    if rows != expected or len(models) != 1 or models[0][0] != model or type(models[0][1]) is not int or models[0][1] <= 0:
        raise ValueError("corpus inesperado")
    return len(rows), models[0][1]


def run_case(case, dbname, params, record, output_path, trace_stream):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    documents = sim.corpus_for(case)
    record["stage"] = "database_create"
    with psycopg.connect(dbname="postgres", **params) as admin:
        if dbname in {row[0] for row in admin.execute("SELECT datname FROM pg_database")}:
            raise ValueError("base ya existente")
        admin.execute(sql.SQL("CREATE DATABASE {} ").format(sql.Identifier(dbname)))
    dsn = make_conninfo(dbname=dbname, **{k: v for k, v in params.items() if k != "autocommit"})
    record["stage"] = "database_initialize"
    with psycopg.connect(dsn, autocommit=True) as conn:
        initialize(conn)
        if conn.execute("SELECT count(*) FROM rag_chunks").fetchone()[0] or conn.execute("SELECT count(*) FROM rag_models").fetchone()[0]:
            raise ValueError("base no vacía")
        record["pgvector_version"] = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]
    write_token, read_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    settings = api.Settings(dsn, write_token, read_token, "ollama", EMBED_MODEL, "ollama", GEN_MODEL, OLLAMA_URL, None)
    server = thread = None
    output = None
    try:
        record["stage"] = "api_start"
        server = local.TestServer(("127.0.0.1", 0), settings)
        record["api_cleanup"] = "unconfirmed"
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        thread.start()
        record["stage"] = "ingestion"
        for doc_id, text in documents.items():
            put_document(server.server_port, write_token, doc_id, text)
        record["documents_indexed"] = len(documents)
        record["stage"] = "database_verify"
        with psycopg.connect(dsn, autocommit=True) as conn:
            record["rows_verified"], record["embedding_dimensions"] = verify_rows(conn, EMBED_MODEL, documents)
        record["stage"] = "capture"
        record["capture_started"] = True
        output, event = capture(case, token=read_token, port=server.server_port, limit=2, timeout_s=180)
        record["capture_outcome"] = event["outcome"]
        record["http_status"] = event["http_status"]
        record["request_id"] = event["request_id"]
        if event["outcome"] not in ("ok_answered", "ok_no_context"):
            raise ValueError("captura no confirmada")
        if not sim._sources_match(output, documents):
            output = _error(case["id"])
            raise ValueError("fuente ajena o alterada")
        if case["attack_source"] is not None:
            record["attack_source_exposed"] = case["attack_source"] in output["sources"]
        raw = _encode(output)
        if any(value.encode("ascii") in raw for value in (read_token, write_token, params["password"])):
            output = _error(case["id"])
            raise ValueError("salida contiene credencial")
        record["output_write_started"] = True
        _append(output_path, raw + b"\n")
        record["output_line_sha256"] = hashlib.sha256(raw).hexdigest()
        record["output_saved"] = True
        record["status"], record["stage"] = "completed", "completed"
    finally:
        if server is not None:
            try:
                if thread is not None and thread.is_alive():
                    server.shutdown()
                server.server_close()
                if thread is not None:
                    thread.join(timeout=10)
                    if thread.is_alive():
                        raise RuntimeError("API no cerró")
                record["api_cleanup"] = "confirmed"
            except Exception:
                record["api_cleanup"] = "unconfirmed"


def execute(cases, records, params, output_path, event_path, trace_stream):
    for index, (case, record) in enumerate(zip(cases, records)):
        record["status"] = "started"
        print(f"Caso {index + 1}/{len(cases)}: {case['id']} iniciado.", file=sys.stderr, flush=True)
        try:
            run_case(case, pg.DB_NAMES[index], params, record, output_path, trace_stream)
            if record["api_cleanup"] != "confirmed":
                raise ValueError("cierre API no confirmado")
        except KeyboardInterrupt:
            record["status"], record["error"] = "failed", "interrupted"
        except Exception:
            record["status"], record["error"] = "failed", "stage_failed"
        if record.get("output_write_started") and not record["output_saved"]:
            record["status"], record["error"] = "failed", "output_write_unconfirmed"
            _append(event_path, _encode(record) + b"\n")
            break
        if not record["output_saved"]:
            try:
                _append(output_path, _encode(_error(case["id"])) + b"\n")
                record["output_saved"] = True
            except OSError:
                record["status"], record["error"] = "failed", "output_write_failed"
        _append(event_path, _encode(record) + b"\n")
        print(f"Caso {index + 1}/{len(cases)}: {record['status']}.", file=sys.stderr, flush=True)
        if record["status"] != "completed":
            break
    return records


def run_check(cases, cases_hash, fixtures_hash, folder):
    import psycopg
    opener = local_opener()
    run_id = secrets.token_hex(16)
    name = "aielab-security-" + run_id
    password = secrets.token_urlsafe(32)
    env = dict(os.environ, POSTGRES_PASSWORD=password)
    records = [{"id": case["id"], "status": "not_started", "stage": None, "error": None,
                "capture_started": False, "output_saved": False, "api_cleanup": "not_created",
                "attack_source_exposed": None} for case in cases]
    report = {"schema_version": 1, "mode": "real_rag_ollama_local", "started_utc": datetime.now(timezone.utc).isoformat(),
              "cases_sha256": cases_hash, "validation_fixtures_sha256": fixtures_hash, "planned": len(cases),
              "cases": records, "status": "failed", "failure_stage": None, "image": pg.IMAGE,
              "container_cleanup": "unconfirmed", "model_digests_before": None, "model_digests_after": None,
              "ollama_url": OLLAMA_URL, "proxy": "direct_no_proxy", "repeats": 1, "warmup": 0,
              "limit": 2, "min_score": None, "external_provider_calls": None, "cross_database_check": "not_started"}
    creation_started = creation_confirmed = False
    network_guards = []
    trace_stream = io.StringIO()
    stage = "preflight"
    try:
        report["model_digests_before"] = model_digests(opener)
        report["image_id"] = pg.docker("image", "inspect", pg.IMAGE, "--format", "{{.Id}}").stdout.decode("ascii").strip()
        paths = [Path(__file__).resolve(), Path(pg.__file__).resolve(), Path(local.__file__).resolve(),
                 ROOT / "01-rag/src/rag/api.py", ROOT / "01-rag/src/rag/answer.py", ROOT / "01-rag/src/rag/ollama.py",
                 ROOT / "01-rag/src/rag/store.py", ROOT / "01-rag/src/rag/ingestion.py", ROOT / "01-rag/src/rag/chunking.py",
                 ROOT / "04-evals/src/evals_lab/security_capture.py", ROOT / "04-evals/src/evals_lab/security_orchestration_sim.py"]
        report["source_sha256"] = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, check=True, timeout=10)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, check=True, timeout=10)
        report["git_head"] = head.stdout.decode("ascii").strip()
        report["worktree_dirty"] = bool(status.stdout.strip())
        report["python_version"] = sys.version.split()[0]
        stage = "container_create"
        creation_started = True
        pg.docker("run", "--detach", "--rm", "--pull", "never", "--name", name,
                  "--label", pg.LABEL + "=" + run_id, "--env", "POSTGRES_PASSWORD",
                  "--tmpfs", "/var/lib/postgresql/data:rw,size=256m", "--publish", "127.0.0.1::5432", pg.IMAGE,
                  env=env, timeout=60)
        creation_confirmed = True
        container = pg.inspect_owned(name, run_id)
        if container.get("Image") != report["image_id"] or container.get("Mounts") or "/var/lib/postgresql/data" not in (container.get("HostConfig", {}).get("Tmpfs") or {}):
            raise ValueError("imagen o almacenamiento inesperado")
        binding = container["NetworkSettings"]["Ports"]["5432/tcp"]
        if len(binding) != 1 or binding[0]["HostIp"] != "127.0.0.1":
            raise ValueError("puerto inesperado")
        report["container_image_storage_loopback_verified"] = True
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
        for provider, effect in ((ollama, lambda request, timeout: checked_urlopen(opener, request, timeout=timeout)),
                                 (openai_client, AssertionError("proveedor externo bloqueado"))):
            guard = patch.object(provider, "urlopen", side_effect=effect)
            network_guards.append((guard, guard.start()))
        stage = "cases"
        with patch.object(api, "Trace", side_effect=lambda enabled: Trace(enabled=True, stream=trace_stream)):
            records = execute(cases, records, params, folder / "outputs.jsonl", folder / "events.jsonl", trace_stream)
        report["cases"] = records
        if any(row["status"] != "completed" for row in records):
            raise ValueError("caso incompleto")
        if network_guards[1][1].call_count:
            raise ValueError("llamada a proveedor externo")
        report["external_provider_calls"] = 0
        stage = "cross_database_check"
        for case, dbname in zip(cases, pg.DB_NAMES):
            with psycopg.connect(dbname=dbname, **params) as conn:
                verify_rows(conn, EMBED_MODEL, sim.corpus_for(case))
        report["cross_database_check"] = "passed"
        stage = "model_identity_after"
        report["model_digests_after"] = model_digests(opener)
        if report["model_digests_after"] != report["model_digests_before"]:
            raise ValueError("identidad de modelo cambió")
        report["status"] = "completed"
    except KeyboardInterrupt:
        report["failure_stage"] = "interrupted"
    except Exception:
        report["failure_stage"] = stage
    finally:
        if any(row["api_cleanup"] == "unconfirmed" for row in report["cases"]):
            report["provider_network_guards"] = "retained_until_process_exit"
        else:
            for guard, _ in reversed(network_guards):
                guard.stop()
        report["container_cleanup"] = pg.cleanup(name, run_id) if creation_started else "not_created"
        if creation_started and not creation_confirmed and report["container_cleanup"] == "confirmed_absent":
            report["container_cleanup"] = "unconfirmed"
        if report["container_cleanup"] not in ("confirmed_removed", "confirmed_absent", "not_created"):
            report["status"] = "failed"
            report["pending_container_name"] = name
            report["pending_container_label"] = pg.LABEL + "=" + run_id
        traces = trace_stream.getvalue().encode("utf-8")
        if password.encode("ascii") in traces:
            report["status"], report["failure_stage"] = "failed", "trace_redaction"
        else:
            try:
                _write_new(folder / "traces.jsonl", traces)
            except OSError:
                report["status"], report["failure_stage"] = "failed", "trace_write_failed"
        report["completed_utc"] = datetime.now(timezone.utc).isoformat()
        report["counts"] = {state: sum(row["status"] == state for row in report["cases"])
                            for state in ("completed", "failed", "started", "not_started")}
        report["outputs_integrity"] = ("unconfirmed" if any(row.get("error") in ("output_write_unconfirmed", "output_write_failed")
                                                              for row in report["cases"]) else "written_without_reported_error")
        for filename in ("cases.jsonl", "outputs.jsonl", "events.jsonl", "traces.jsonl"):
            path = folder / filename
            if path.exists():
                report.setdefault("artifact_sha256", {})[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    return report


def main():
    parser = argparse.ArgumentParser(description="Ejecuta casos sintéticos con Ollama local.")
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    folder = None
    try:
        label = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
        folder = ROOT / "04-evals/eval_data/rag_baseline" / ("security-ollama-" + label)
        folder.mkdir()
        _write_new(folder / "cases.jsonl", (ROOT / "04-evals/examples/security_cases.jsonl").read_bytes())
        _write_new(folder / "outputs.jsonl", b"")
        _write_new(folder / "events.jsonl", b"")
        cases, _, cases_hash, fixtures_hash = sim.load_inputs(folder / "cases.jsonl", ROOT / "04-evals/examples/security_outputs.jsonl")
        print(f"Directorio de la ejecución: {folder}", file=sys.stderr, flush=True)
        report = run_check(cases, cases_hash, fixtures_hash, folder)
        _write_new(folder / "report.json", _encode(report) + b"\n")
        print(json.dumps({"status": report["status"], "counts": report["counts"],
                          "container_cleanup": report["container_cleanup"], "report": str(folder / "report.json")}, ensure_ascii=True))
        return 0 if report["status"] == "completed" else 1
    except Exception:
        print("No se pudo completar la ejecución local; revisa los artefactos y recursos pendientes.", file=sys.stderr)
        if folder is not None:
            print(f"Directorio de la ejecución: {folder}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
