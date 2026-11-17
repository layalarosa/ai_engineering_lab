"""Integra 24 intentos con API RAG/PostgreSQL reales y proveedores ficticios."""
import argparse
import hashlib
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
import rag_api_five_cases_fake_model_check as five
import rag_api_postgres_fake_model_check as one
from evals_lab import security_expanded_simulation as expanded
from evals_lab.security_capture import _encode, _error, capture
from rag import answer, api, ollama, openai_client
from rag.observability import Trace
from rag.store import initialize

ROOT = pg.ROOT
DB_NAMES = tuple(f"expanded_case_{i:02d}" for i in range(12))
INPUTS = {"input-cases.jsonl": "security_expanded_cases.jsonl", "input-corpus.jsonl": "security_expanded_corpus.jsonl",
          "input-expectations.jsonl": "security_control_expectations.jsonl", "input-fixtures.jsonl": "security_expanded_simulated_outputs.jsonl"}


def initial_records(attempts):
    return [{"id": row["plan"]["id"], "scenario_id": row["plan"]["scenario_id"],
             "repetition": row["plan"]["repetition"], "kind": row["plan"]["kind"],
             "status": "not_started", "stage": None, "error": None, "capture_started": False,
             "capture_outcome": None, "output_saved": False, "api_cleanup": "not_created"} for row in attempts]


def exercise_attempt(item, params, initialized, record, output_path):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    plan, case, fixture = item["plan"], item["case"], item["fixture"]
    documents = {s["document_id"]: s["text"] for s in item["documents"]}
    dbname = DB_NAMES[expanded.SCENARIO_IDS.index(plan["scenario_id"])]
    first = plan["repetition"] == 1
    if first:
        record["stage"] = "database_create"
        with psycopg.connect(dbname="postgres", **params) as admin:
            if dbname in {row[0] for row in admin.execute("SELECT datname FROM pg_database")}:
                raise ValueError("database already exists")
            admin.execute(sql.SQL("CREATE DATABASE {} ").format(sql.Identifier(dbname)))
    elif plan["scenario_id"] not in initialized:
        raise ValueError("scenario database not initialized")
    dsn = make_conninfo(dbname=dbname, **{key:value for key,value in params.items() if key != "autocommit"})
    if first:
        record["stage"] = "database_initialize"
        with psycopg.connect(dsn, autocommit=True) as conn:
            initialize(conn)
            if conn.execute("SELECT count(*) FROM rag_chunks").fetchone()[0] or conn.execute("SELECT count(*) FROM rag_models").fetchone()[0]:
                raise ValueError("database not empty")
            record["pgvector_version"] = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]
    write_token, read_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    while read_token == write_token:
        read_token = secrets.token_urlsafe(32)
    settings = api.Settings(dsn, write_token, read_token, "ollama", one.MODEL, "ollama", "fake-generation", one.URL, None)
    server = thread = None
    received = []
    with ExitStack() as stack:
        embeddings = stack.enter_context(patch.object(ollama, "embed", side_effect=one.fake_embedding))
        generation = stack.enter_context(patch.object(answer, "generate", side_effect=five.generation_for(case, documents, fixture, received)))
        try:
            record["stage"] = "api_start"
            server = one.TestServer(("127.0.0.1", 0), settings)
            record["api_cleanup"] = "unconfirmed"
            thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
            thread.start()
            if first:
                record["stage"] = "api_ingestion"
                for doc_id, text in documents.items():
                    one.put_document(server.server_port, write_token, doc_id, text)
                record["documents_indexed"] = len(documents)
            else:
                record["documents_indexed"] = 0
            record["stage"] = "database_verify"
            with psycopg.connect(dsn, autocommit=True) as conn:
                record["rows_verified"] = five.verify_rows(conn, documents)
            if first:
                initialized.add(plan["scenario_id"])
            record["stage"], record["capture_started"] = "api_answer_capture", True
            output, event = capture(case, token=read_token, port=server.server_port, limit=2, timeout_s=10)
            record["capture_outcome"] = event["outcome"]
            expected = {(doc_id, 0, text) for doc_id, text in documents.items()}
            actual = {(s["document_id"], s["chunk_index"], s["text"]) for s in output["sources"] or []}
            context = [{"etiqueta": f"[{i}]", "texto": s["text"]} for i, s in enumerate(output["sources"] or [], 1)]
            if (event["outcome"] != "ok_answered" or output["answer"] != fixture["answer"] or
                    actual != expected or len(output["sources"]) != len(expected) or received != [context] or
                    embeddings.call_count != (len(documents) if first else 0) + 1 or generation.call_count != 1):
                raise ValueError("unexpected response or corpus")
            raw = _encode(output)
            if any(value.encode("ascii") in raw for value in (read_token, write_token, params["password"])):
                raise ValueError("credential in output")
            record["context_and_sources_verified"] = True
            record["fake_embedding_calls"], record["fake_generation_calls"] = embeddings.call_count, generation.call_count
            record["stage"], record["output_write_started"] = "output_write", True
            expanded._append(output_path, raw + b"\n")
            record["output_saved"] = True
            record["output_line_sha256"] = hashlib.sha256(raw).hexdigest()
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


def execute(attempts, records, params, output_path, events_path):
    initialized = set()
    output_integrity = event_integrity = "confirmed"
    for item, record in zip(attempts, records):
        record["status"], record["stage"] = "started", "begin"
        try:
            expanded._append(events_path, _encode({"id":record["id"],"event":"started"}) + b"\n")
        except OSError:
            record["status"],record["error"],event_integrity = "failed","event_write_unconfirmed","unconfirmed"
            break
        try:
            exercise_attempt(item, params, initialized, record, output_path)
            if record["api_cleanup"] != "confirmed":
                raise ValueError("API cleanup unconfirmed")
            record["status"], record["stage"] = "completed", "completed"
        except KeyboardInterrupt:
            record["status"], record["error"] = "failed", "interrupted"
        except Exception:
            record["status"], record["error"] = "failed", "stage_failed"
        if record["api_cleanup"] == "unconfirmed":
            record["status"], record["error"] = "failed", "cleanup_unconfirmed"
        if record.get("output_write_started") and not record["output_saved"]:
            record["status"], record["error"], output_integrity = "failed", "output_write_unconfirmed", "unconfirmed"
        elif not record["output_saved"]:
            try:
                expanded._append(output_path, _encode(_error(record["id"])) + b"\n")
                record["output_saved"] = True
            except OSError:
                record["status"], record["error"], output_integrity = "failed", "output_write_unconfirmed", "unconfirmed"
        try:
            expanded._append(events_path, _encode({"id":record["id"],"event":"finished","status":record["status"],
                                                   "capture_started":record["capture_started"],"error":record["error"]}) + b"\n")
        except OSError:
            record["status"],record["error"],event_integrity = "failed","event_write_unconfirmed","unconfirmed"
        if record["status"] != "completed":
            break
    return output_integrity, event_integrity, initialized


def run(attempts, identities, folder):
    import psycopg

    run_id = secrets.token_hex(16)
    name = "aielab-security-" + run_id
    password = secrets.token_urlsafe(32)
    env = dict(os.environ, POSTGRES_PASSWORD=password)
    records = initial_records(attempts)
    report = {"schema_version":1,"mode":"expanded_real_rag_postgres_fake_providers","status":"failed",
              "started_utc":datetime.now(timezone.utc).isoformat(),"planned":len(attempts),"attempts":records,
              "input_sha256":{k:identities[k] for k in ("cases_sha256","corpus_sha256","expectations_sha256","fixtures_sha256")},
              "image":pg.IMAGE,"failure_stage":None,"container_cleanup":"unconfirmed","cross_database_check":"not_started",
              "external_provider_calls":None,"output_integrity":"confirmed","event_integrity":"confirmed"}
    stage = "image_check"
    creation_started = creation_confirmed = False
    guards = []
    try:
        report["image_id"] = pg.docker("image","inspect",pg.IMAGE,"--format","{{.Id}}").stdout.decode("ascii").strip()
        paths = [Path(__file__).resolve(),Path(pg.__file__).resolve(),Path(one.__file__).resolve(),Path(five.__file__).resolve(),
                 ROOT/"01-rag/src/rag/api.py",ROOT/"01-rag/src/rag/answer.py",ROOT/"01-rag/src/rag/store.py",
                 ROOT/"01-rag/src/rag/ollama.py",ROOT/"01-rag/src/rag/ingestion.py",ROOT/"01-rag/src/rag/chunking.py",
                 ROOT/"04-evals/src/evals_lab/security_capture.py",ROOT/"04-evals/src/evals_lab/security_expanded_simulation.py"]
        report["source_sha256"] = {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        stage = "container_create"
        creation_started = True
        pg.docker("run","--detach","--rm","--pull","never","--name",name,"--label",pg.LABEL+"="+run_id,
                  "--env","POSTGRES_PASSWORD","--tmpfs","/var/lib/postgresql/data:rw,size=256m",
                  "--publish","127.0.0.1::5432",pg.IMAGE,env=env,timeout=60)
        creation_confirmed = True
        container = pg.inspect_owned(name,run_id)
        if container.get("Image") != report["image_id"] or container.get("Mounts") or "/var/lib/postgresql/data" not in (container.get("HostConfig",{}).get("Tmpfs") or {}):
            raise ValueError("unexpected image or storage")
        binding = container["NetworkSettings"]["Ports"]["5432/tcp"]
        if len(binding)!=1 or binding[0]["HostIp"]!="127.0.0.1":
            raise ValueError("unexpected network binding")
        report["container_image_storage_loopback_verified"] = True
        params = {"host":"127.0.0.1","port":int(binding[0]["HostPort"]),"user":"postgres","password":password,
                  "connect_timeout":2,"autocommit":True}
        stage = "postgres_ready"
        deadline = time.monotonic()+60
        while True:
            try:
                conn=psycopg.connect(dbname="postgres",**params)
                conn.close()
                break
            except psycopg.Error:
                if time.monotonic()>=deadline:
                    raise
                time.sleep(.5)
        for provider in (ollama,openai_client):
            guard=patch.object(provider,"urlopen",side_effect=AssertionError("model access blocked"))
            guards.append((guard,guard.start()))
        stage = "attempts"
        with patch.object(api,"Trace",side_effect=lambda enabled:Trace(enabled=False)):
            out_ok,event_ok,initialized=execute(attempts,records,params,folder/"outputs.jsonl",folder/"events.jsonl")
        report["output_integrity"],report["event_integrity"] = out_ok,event_ok
        if any(record["status"]!="completed" for record in records):
            raise ValueError("incomplete attempts")
        if any(mock.call_count for _,mock in guards):
            raise ValueError("unexpected provider calls")
        report["external_provider_calls"] = 0
        stage = "cross_database_check"
        if initialized != set(expanded.SCENARIO_IDS):
            raise ValueError("unexpected databases")
        for scenario,dbname in zip(attempts[:12],DB_NAMES):
            docs={source["document_id"]:source["text"] for source in scenario["documents"]}
            with psycopg.connect(dbname=dbname,**params) as conn:
                five.verify_rows(conn,docs)
        report["cross_database_check"] = "passed"
        report["status"] = "completed"
    except KeyboardInterrupt:
        report["failure_stage"] = "interrupted"
    except Exception:
        report["failure_stage"] = stage
    finally:
        if any(r["api_cleanup"]=="unconfirmed" for r in records):
            report["provider_network_guards"] = "retained_until_process_exit"
        else:
            for guard,_ in reversed(guards):
                guard.stop()
        report["container_cleanup"] = pg.cleanup(name,run_id) if creation_started else "not_created"
        if creation_started and not creation_confirmed and report["container_cleanup"]=="confirmed_absent":
            report["container_cleanup"] = "unconfirmed"
        if report["container_cleanup"] not in ("confirmed_removed","confirmed_absent","not_created"):
            report["status"] = "failed"
            report["pending_container_name"],report["pending_container_label"] = name,pg.LABEL+"="+run_id
        report["completed_utc"] = datetime.now(timezone.utc).isoformat()
        report["attempted"] = sum(r["capture_started"] for r in records)
        report["counts"] = {key:sum(r["status"]==key for r in records) for key in ("completed","failed","started","not_started")}
        report["artifact_sha256"] = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()}
    return report


def main():
    parser=argparse.ArgumentParser(description="24 intentos en RAG/PostgreSQL con proveedores simulados.")
    parser.add_argument("--run",action="store_true",required=True)
    parser.parse_args()
    folder=None
    try:
        label=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+secrets.token_hex(4)
        folder=ROOT/"04-evals/eval_data/rag_baseline"/("expanded-rag-fake-"+label)
        folder.mkdir()
        for output,source in INPUTS.items():
            expanded._write_new(folder/output,(ROOT/"04-evals/examples"/source).read_bytes())
        cases,corpus,expectations,fixtures,ids=expanded.load_inputs(*(folder/name for name in INPUTS))
        if any(row["status"]!="answered" for row in fixtures):
            raise ValueError("unexpected fixture status")
        attempts,materialized,control_exps=expanded.build_plan(cases,corpus,expectations,fixtures,ids)
        for name,rows in (("plan.jsonl",[item["plan"] for item in attempts]),("cases.jsonl",materialized),
                          ("expectations.jsonl",control_exps),("outputs.jsonl",[]),("events.jsonl",[]),
                          ("attack_reviews.jsonl",[]),("control_reviews.jsonl",[])):
            expanded._write_new(folder/name,expanded._jsonl(rows))
        print(f"Directorio de la ejecución: {folder}",file=sys.stderr,flush=True)
        report=run(attempts,ids,folder)
        expanded._write_new(folder/"report.json",_encode(report)+b"\n")
        print(json.dumps({"status":report["status"],"counts":report["counts"],"attempted":report["attempted"],
                          "container_cleanup":report["container_cleanup"],"report":str(folder/"report.json")},ensure_ascii=True))
        return 0 if report["status"]=="completed" else 1
    except Exception:
        print("No se pudo completar la integración; revisa los artefactos y recursos pendientes.",file=sys.stderr)
        if folder is not None:
            print(f"Directorio de la ejecución: {folder}",file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
