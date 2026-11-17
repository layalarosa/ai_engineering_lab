"""Ejecuta 24 intentos sintéticos contra RAG y Ollama local; requiere --run."""
import argparse
import copy
import hashlib
import io
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import postgres_isolation_check as pg
import rag_api_postgres_fake_model_check as api_helper
import rag_expanded_postgres_fake_run as orchestration
import rag_security_ollama_local_run as local
from evals_lab import security_expanded_simulation as expanded
from evals_lab import security_orchestration_sim as sim
from evals_lab.cases import InputError, _read_jsonl
from evals_lab.security import _case
from evals_lab.security_capture import _encode, capture
from evals_lab.security_controls import _expectation
from rag import api, ollama, openai_client
from rag.observability import Trace
from rag.store import initialize

ROOT = pg.ROOT
INPUTS = {"input-cases.jsonl":"security_expanded_cases.jsonl", "input-corpus.jsonl":"security_expanded_corpus.jsonl",
          "input-expectations.jsonl":"security_control_expectations.jsonl"}


def load_inputs(cases_path, corpus_path, expectations_path):
    case_lines, corpus_lines, expectation_lines = {}, {}, {}
    cases, cases_hash = _read_jsonl(cases_path,kind="casos",keys={"id","input","kind","attack_source","criterion"},
                                    allow_empty=False,validate=_case,allow_integers=True,line_hashes=case_lines)
    corpus, corpus_hash = _read_jsonl(corpus_path,kind="corpus",keys={"id","documents"},
                                      allow_empty=False,validate=expanded._corpus,allow_integers=True,line_hashes=corpus_lines)
    expectations, expectations_hash = _read_jsonl(expectations_path,kind="expectativas",keys={"id","expected_sources"},
                                                   allow_empty=False,validate=_expectation,allow_integers=True,line_hashes=expectation_lines)
    if tuple(row["id"] for row in cases)!=expanded.SCENARIO_IDS or tuple(row["id"] for row in corpus)!=expanded.SCENARIO_IDS:
        raise InputError("Los casos y corpus deben contener los 12 escenarios en orden.")
    controls={case["id"] for case in cases if case["kind"]=="control"}
    if controls!={f"f{family}_c" for family in range(1,5)} or {row["id"] for row in expectations}!=controls:
        raise InputError("Los tipos o expectativas no corresponden a los controles previstos.")
    by_expectation={row["id"]:row for row in expectations}
    for case,dataset in zip(cases,corpus):
        if case["kind"]=="attack" and case["attack_source"] not in dataset["documents"]:
            raise InputError("La fuente adversarial no pertenece al corpus del escenario.")
        if case["kind"]=="control" and any(s not in dataset["documents"] for s in by_expectation[case["id"]]["expected_sources"]):
            raise InputError("Las fuentes esperadas no pertenecen al corpus del control.")
    return cases,corpus,expectations,{"cases_sha256":cases_hash,"corpus_sha256":corpus_hash,
                                       "expectations_sha256":expectations_hash,"case_lines":case_lines,
                                       "corpus_lines":corpus_lines,"expectation_lines":expectation_lines}


def build_plan(cases,corpus,expectations,identities):
    by_expectation={row["id"]:row for row in expectations}
    attempts,materialized,controls=[],[],[]
    for repetition in range(1,3):
        for case,dataset in zip(cases,corpus):
            case_id=f"{case['id']}_r{repetition}"
            cloned=copy.deepcopy(case); cloned["id"]=case_id; _case(cloned)
            expected=copy.deepcopy(by_expectation[case["id"]]["expected_sources"]) if case["kind"]=="control" else [copy.deepcopy(case["attack_source"])]
            plan={"id":case_id,"scenario_id":case["id"],"repetition":repetition,"kind":case["kind"],
                  "scenario_line_sha256":identities["case_lines"][case["id"]],
                  "corpus_line_sha256":identities["corpus_lines"][case["id"]],
                  "expectation_line_sha256":identities["expectation_lines"].get(case["id"])}
            attempts.append({"plan":plan,"case":cloned,"documents":copy.deepcopy(dataset["documents"]),"expected_sources":expected})
            materialized.append(cloned)
            if case["kind"]=="control":
                expectation=copy.deepcopy(by_expectation[case["id"]]); expectation["id"]=case_id; _expectation(expectation)
                controls.append(expectation)
    return attempts,materialized,controls


def validate_output(item,output,secrets_seen):
    documents={source["document_id"]:source["text"] for source in item["documents"]}
    if not sim._sources_match(output,documents):
        raise ValueError("unexpected source")
    exposed=all(source in output["sources"] for source in item["expected_sources"])
    raw=_encode(output)
    if any(secret.encode("ascii") in raw for secret in secrets_seen):
        raise ValueError("credential in output")
    return exposed,raw


def exercise_attempt(item,params,initialized,record,output_path,secrets_seen):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    plan,case=item["plan"],item["case"]
    documents={source["document_id"]:source["text"] for source in item["documents"]}
    dbname=orchestration.DB_NAMES[expanded.SCENARIO_IDS.index(plan["scenario_id"])]
    first=plan["repetition"]==1
    if first:
        record["stage"]="database_create"
        with psycopg.connect(dbname="postgres",**params) as admin:
            if dbname in {row[0] for row in admin.execute("SELECT datname FROM pg_database")}:
                raise ValueError("database already exists")
            admin.execute(sql.SQL("CREATE DATABASE {} ").format(sql.Identifier(dbname)))
    elif plan["scenario_id"] not in initialized:
        raise ValueError("scenario database not initialized")
    dsn=make_conninfo(dbname=dbname,**{key:value for key,value in params.items() if key!="autocommit"})
    if first:
        record["stage"]="database_initialize"
        with psycopg.connect(dsn,autocommit=True) as conn:
            initialize(conn)
            if conn.execute("SELECT count(*) FROM rag_chunks").fetchone()[0] or conn.execute("SELECT count(*) FROM rag_models").fetchone()[0]:
                raise ValueError("database not empty")
            record["pgvector_version"]=conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]
    write_token,read_token=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    while read_token==write_token:
        read_token=secrets.token_urlsafe(32)
    secrets_seen.extend((write_token,read_token))
    settings=api.Settings(dsn,write_token,read_token,"ollama",local.EMBED_MODEL,"ollama",local.GEN_MODEL,local.OLLAMA_URL,None)
    server=thread=None
    try:
        record["stage"]="api_start"
        server=api_helper.TestServer(("127.0.0.1",0),settings)
        record["api_cleanup"]="unconfirmed"
        thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.01),daemon=True)
        thread.start()
        if first:
            record["stage"]="api_ingestion"
            for doc_id,text in documents.items():
                local.put_document(server.server_port,write_token,doc_id,text)
            record["documents_indexed"]=len(documents)
        else:
            record["documents_indexed"]=0
        record["stage"]="database_verify"
        with psycopg.connect(dsn,autocommit=True) as conn:
            record["rows_verified"],record["embedding_dimensions"]=local.verify_rows(conn,local.EMBED_MODEL,documents)
        if first:
            initialized.add(plan["scenario_id"])
        record["stage"],record["capture_started"]="api_answer_capture",True
        output,event=capture(case,token=read_token,port=server.server_port,limit=2,timeout_s=180)
        record["capture_outcome"],record["http_status"],record["request_id"]=event["outcome"],event["http_status"],event["request_id"]
        if event["outcome"] not in ("ok_answered","ok_no_context"):
            raise ValueError("capture failed")
        record["expected_sources_exposed"],raw=validate_output(item,output,secrets_seen)
        record["stage"],record["output_write_started"]="output_write",True
        expanded._append(output_path,raw+b"\n")
        record["output_saved"]=True
        record["output_line_sha256"]=hashlib.sha256(raw).hexdigest()
    finally:
        if server is not None:
            try:
                if thread is not None and thread.is_alive():
                    server.shutdown()
                server.server_close()
                if thread is not None:
                    thread.join(timeout=10)
                    if thread.is_alive(): raise RuntimeError("server thread remains")
                record["api_cleanup"]="confirmed"
            except Exception:
                record["api_cleanup"]="unconfirmed"


def run(attempts,identities,folder):
    import psycopg

    opener=local.local_opener()
    run_id=secrets.token_hex(16)
    name="aielab-security-"+run_id
    password=secrets.token_urlsafe(32)
    secrets_seen=[password]
    env=dict(os.environ,POSTGRES_PASSWORD=password)
    records=orchestration.initial_records(attempts)
    report={"schema_version":1,"mode":"expanded_real_rag_ollama_local","status":"failed",
            "started_utc":datetime.now(timezone.utc).isoformat(),"planned":len(attempts),"attempts":records,
            "input_sha256":{key:identities[key] for key in ("cases_sha256","corpus_sha256","expectations_sha256")},
            "image":pg.IMAGE,"ollama_url":local.OLLAMA_URL,"embedding_model":local.EMBED_MODEL,"generation_model":local.GEN_MODEL,
            "proxy":"direct_no_proxy","repeats":2,"warmup":0,"limit":2,"min_score":None,
            "model_digests_before":None,"model_digests_after":None,"external_provider_calls":None,
            "failure_stage":None,"container_cleanup":"unconfirmed","cross_database_check":"not_started",
            "output_integrity":"confirmed","event_integrity":"confirmed"}
    stage="preflight"
    creation_started=creation_confirmed=False
    guards=[]
    trace_stream=io.StringIO()
    request_counts={"embed":0,"generate":0}
    try:
        report["model_digests_before"]=local.model_digests(opener)
        report["image_id"]=pg.docker("image","inspect",pg.IMAGE,"--format","{{.Id}}").stdout.decode("ascii").strip()
        paths=[Path(__file__).resolve(),Path(pg.__file__).resolve(),Path(api_helper.__file__).resolve(),
               Path(orchestration.__file__).resolve(),Path(local.__file__).resolve(),
               ROOT/"01-rag/src/rag/api.py",ROOT/"01-rag/src/rag/answer.py",ROOT/"01-rag/src/rag/ollama.py",
               ROOT/"01-rag/src/rag/store.py",ROOT/"01-rag/src/rag/ingestion.py",ROOT/"01-rag/src/rag/chunking.py",
               ROOT/"04-evals/src/evals_lab/security_capture.py",ROOT/"04-evals/src/evals_lab/security_expanded_simulation.py"]
        report["source_sha256"]={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        head=subprocess.run(["git","rev-parse","HEAD"],cwd=ROOT,capture_output=True,check=True,timeout=10)
        status=subprocess.run(["git","status","--porcelain"],cwd=ROOT,capture_output=True,check=True,timeout=10)
        report["git_head"],report["worktree_dirty"]=head.stdout.decode("ascii").strip(),bool(status.stdout.strip())
        report["python_version"]=sys.version.split()[0]
        stage="container_create"
        creation_started=True
        pg.docker("run","--detach","--rm","--pull","never","--name",name,"--label",pg.LABEL+"="+run_id,
                  "--env","POSTGRES_PASSWORD","--tmpfs","/var/lib/postgresql/data:rw,size=256m",
                  "--publish","127.0.0.1::5432",pg.IMAGE,env=env,timeout=60)
        creation_confirmed=True
        container=pg.inspect_owned(name,run_id)
        if container.get("Image")!=report["image_id"] or container.get("Mounts") or "/var/lib/postgresql/data" not in (container.get("HostConfig",{}).get("Tmpfs") or {}):
            raise ValueError("unexpected image or storage")
        binding=container["NetworkSettings"]["Ports"]["5432/tcp"]
        if len(binding)!=1 or binding[0]["HostIp"]!="127.0.0.1": raise ValueError("unexpected network binding")
        report["container_image_storage_loopback_verified"]=True
        params={"host":"127.0.0.1","port":int(binding[0]["HostPort"]),"user":"postgres","password":password,
                "connect_timeout":2,"autocommit":True}
        stage="postgres_ready"
        deadline=time.monotonic()+60
        while True:
            try:
                conn=psycopg.connect(dbname="postgres",**params); conn.close(); break
            except psycopg.Error:
                if time.monotonic()>=deadline: raise
                time.sleep(.5)
        def local_request(request,timeout):
            import urllib.parse
            path=urllib.parse.urlsplit(request.full_url).path
            if path=="/api/embed": request_counts["embed"]+=1
            elif path=="/api/generate": request_counts["generate"]+=1
            return local.checked_urlopen(opener,request,timeout=timeout)
        for provider,effect in ((ollama,local_request),(openai_client,AssertionError("external provider blocked"))):
            guard=patch.object(provider,"urlopen",side_effect=effect)
            guards.append((guard,guard.start()))
        stage="attempts"
        def tracked(item,params,initialized,record,output_path):
            position=(item["plan"]["repetition"]-1)*12+expanded.SCENARIO_IDS.index(item["plan"]["scenario_id"])+1
            print(f"Intento {position}/24: {item['plan']['id']} iniciado.",file=sys.stderr,flush=True)
            exercise_attempt(item,params,initialized,record,output_path,secrets_seen)
            print(f"Intento {position}/24: captura finalizada.",file=sys.stderr,flush=True)
        with patch.object(api,"Trace",side_effect=lambda enabled:Trace(enabled=True,stream=trace_stream)), \
             patch.object(orchestration,"exercise_attempt",side_effect=tracked):
            out_ok,event_ok,initialized=orchestration.execute(attempts,records,params,folder/"outputs.jsonl",folder/"events.jsonl")
        report["output_integrity"],report["event_integrity"]=out_ok,event_ok
        if any(record["status"]!="completed" for record in records): raise ValueError("incomplete attempts")
        if guards[1][1].call_count: raise ValueError("unexpected external provider")
        report["external_provider_calls"]=0
        report["ollama_requests"]=request_counts
        stage="cross_database_check"
        if initialized!=set(expanded.SCENARIO_IDS): raise ValueError("unexpected databases")
        for item,dbname in zip(attempts[:12],orchestration.DB_NAMES):
            documents={source["document_id"]:source["text"] for source in item["documents"]}
            with psycopg.connect(dbname=dbname,**params) as conn:
                local.verify_rows(conn,local.EMBED_MODEL,documents)
        report["cross_database_check"]="passed"
        stage="model_identity_after"
        report["model_digests_after"]=local.model_digests(opener)
        if report["model_digests_before"]!=report["model_digests_after"]: raise ValueError("model identity changed")
        report["status"]="completed"
    except KeyboardInterrupt:
        report["failure_stage"]="interrupted"
    except Exception:
        report["failure_stage"]=stage
    finally:
        if any(row["api_cleanup"]=="unconfirmed" for row in records):
            report["provider_network_guards"]="retained_until_process_exit"
        else:
            for guard,_ in reversed(guards): guard.stop()
        report["container_cleanup"]=pg.cleanup(name,run_id) if creation_started else "not_created"
        if creation_started and not creation_confirmed and report["container_cleanup"]=="confirmed_absent":
            report["container_cleanup"]="unconfirmed"
        if report["container_cleanup"] not in ("confirmed_removed","confirmed_absent","not_created"):
            report["status"]="failed"
            report["pending_container_name"],report["pending_container_label"]=name,pg.LABEL+"="+run_id
        traces=trace_stream.getvalue().encode("utf-8")
        if any(secret.encode("ascii") in traces for secret in secrets_seen):
            report["status"],report["failure_stage"]="failed","trace_redaction"
        else:
            try: expanded._write_new(folder/"traces.jsonl",traces)
            except OSError: report["status"],report["failure_stage"]="failed","trace_write_failed"
        report["completed_utc"]=datetime.now(timezone.utc).isoformat()
        report["attempted"]=sum(row["capture_started"] for row in records)
        report["counts"]={key:sum(row["status"]==key for row in records) for key in ("completed","failed","started","not_started")}
        report["artifact_sha256"]={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()}
    return report


def main():
    parser=argparse.ArgumentParser(description="24 intentos sintéticos RAG con Ollama local.")
    parser.add_argument("--run",action="store_true",required=True)
    parser.parse_args()
    folder=None
    try:
        label=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+secrets.token_hex(4)
        folder=ROOT/"04-evals/eval_data/rag_baseline"/("expanded-ollama-"+label)
        folder.mkdir()
        for output,source in INPUTS.items():
            expanded._write_new(folder/output,(ROOT/"04-evals/examples"/source).read_bytes())
        cases,corpus,expectations,ids=load_inputs(*(folder/name for name in INPUTS))
        attempts,materialized,controls=build_plan(cases,corpus,expectations,ids)
        for name,rows in (("plan.jsonl",[item["plan"] for item in attempts]),("cases.jsonl",materialized),
                          ("expectations.jsonl",controls),("outputs.jsonl",[]),("events.jsonl",[]),
                          ("attack_reviews.jsonl",[]),("control_reviews.jsonl",[])):
            expanded._write_new(folder/name,expanded._jsonl(rows))
        print(f"Directorio de la ejecución: {folder}",file=sys.stderr,flush=True)
        report=run(attempts,ids,folder)
        expanded._write_new(folder/"report.json",_encode(report)+b"\n")
        print(json.dumps({"status":report["status"],"counts":report["counts"],"attempted":report["attempted"],
                          "container_cleanup":report["container_cleanup"],"report":str(folder/"report.json")},ensure_ascii=True))
        return 0 if report["status"]=="completed" else 1
    except Exception:
        print("No se pudo completar la ejecución local; revisa los artefactos y recursos pendientes.",file=sys.stderr)
        if folder is not None: print(f"Directorio de la ejecución: {folder}",file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
