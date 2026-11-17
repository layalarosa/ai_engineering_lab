"""Orquesta 24 intentos sintéticos con API HTTP simulada y sin modelos."""
import argparse
import copy
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path

from .cases import InputError, MAX_FILE_BYTES, MAX_LINE_BYTES, _read_jsonl
from .grounding import _id, _items
from .security import _case, _output, _source
from .security_capture import _encode, _error, capture
from .security_controls import _expectation
from .security_orchestration_sim import _sources_match, _start_server, _stop_server

SCENARIO_IDS = tuple(f"f{family}_{variant}" for family in range(1, 5) for variant in ("c", "a", "b"))
REPETITIONS = 2


def _corpus(row):
    _id(row["id"])
    _items(row["documents"])
    if not 1 <= len(row["documents"]) <= 2:
        raise ValueError
    seen = set()
    for source in row["documents"]:
        _source(source)
        key = (source["document_id"], source["chunk_index"])
        if key in seen or source["chunk_index"] != 0 or len(source["text"]) > 500:
            raise ValueError
        seen.add(key)


def load_inputs(cases_path, corpus_path, expectations_path, fixtures_path):
    case_lines, corpus_lines, expectation_lines, fixture_lines = {}, {}, {}, {}
    cases, cases_hash = _read_jsonl(cases_path, kind="casos", keys={"id", "input", "kind", "attack_source", "criterion"},
                                    allow_empty=False, validate=_case, allow_integers=True, line_hashes=case_lines)
    corpus, corpus_hash = _read_jsonl(corpus_path, kind="corpus", keys={"id", "documents"},
                                      allow_empty=False, validate=_corpus, allow_integers=True, line_hashes=corpus_lines)
    expectations, expectations_hash = _read_jsonl(expectations_path, kind="expectativas", keys={"id", "expected_sources"},
                                                   allow_empty=False, validate=_expectation, allow_integers=True, line_hashes=expectation_lines)
    fixtures, fixtures_hash = _read_jsonl(fixtures_path, kind="respuestas simuladas",
                                          keys={"id", "status", "answer", "sources", "sources_complete"},
                                          allow_empty=False, validate=_output, allow_integers=True, line_hashes=fixture_lines)
    if any(tuple(row["id"] for row in rows) != SCENARIO_IDS for rows in (cases, corpus, fixtures)):
        raise InputError("Los casos, corpus y respuestas deben contener los 12 escenarios en orden.")
    controls = {case["id"] for case in cases if case["kind"] == "control"}
    if controls != {f"f{family}_c" for family in range(1, 5)} or {row["id"] for row in expectations} != controls:
        raise InputError("Los tipos o expectativas no corresponden a los controles previstos.")
    by_expectation = {row["id"]: row for row in expectations}
    for case, dataset, fixture in zip(cases, corpus, fixtures):
        documents = dataset["documents"]
        if case["kind"] == "attack" and case["attack_source"] not in documents:
            raise InputError("La fuente adversarial no pertenece al corpus del escenario.")
        if case["kind"] == "control" and any(source not in documents for source in by_expectation[case["id"]]["expected_sources"]):
            raise InputError("Las fuentes esperadas no pertenecen al corpus del control.")
        if fixture["status"] != "error" and (not fixture["answer"].strip() or not fixture["sources_complete"] or
                                              any(source not in documents for source in fixture["sources"])):
            raise InputError("Las fuentes simuladas no corresponden al corpus del escenario.")
    return cases, corpus, expectations, fixtures, {
        "cases_sha256": cases_hash, "corpus_sha256": corpus_hash,
        "expectations_sha256": expectations_hash, "fixtures_sha256": fixtures_hash,
        "case_lines": case_lines, "corpus_lines": corpus_lines,
        "expectation_lines": expectation_lines, "fixture_lines": fixture_lines}


def build_plan(cases, corpus, expectations, fixtures, identities):
    by_expectation = {row["id"]: row for row in expectations}
    attempts, materialized_cases, materialized_expectations = [], [], []
    for repetition in range(1, REPETITIONS + 1):
        for case, dataset, fixture in zip(cases, corpus, fixtures):
            case_id = f"{case['id']}_r{repetition}"
            _id(case_id)
            materialized = copy.deepcopy(case)
            materialized["id"] = case_id
            _case(materialized)
            plan = {"id": case_id, "scenario_id": case["id"], "repetition": repetition, "kind": case["kind"],
                    "scenario_line_sha256": identities["case_lines"][case["id"]],
                    "corpus_line_sha256": identities["corpus_lines"][case["id"]],
                    "fixture_line_sha256": identities["fixture_lines"][case["id"]],
                    "expectation_line_sha256": identities["expectation_lines"].get(case["id"])}
            attempts.append({"plan": plan, "case": materialized, "documents": copy.deepcopy(dataset["documents"]),
                             "fixture": copy.deepcopy(fixture)})
            materialized_cases.append(materialized)
            if case["kind"] == "control":
                expectation = copy.deepcopy(by_expectation[case["id"]])
                expectation["id"] = case_id
                _expectation(expectation)
                materialized_expectations.append(expectation)
    return attempts, materialized_cases, materialized_expectations


def _jsonl(rows):
    lines = [_encode(row) for row in rows]
    if any(len(line) > MAX_LINE_BYTES for line in lines):
        raise InputError("Una línea generada supera el límite del evaluador.")
    raw = b"".join(line + b"\n" for line in lines)
    if len(raw) > MAX_FILE_BYTES:
        raise InputError("Un archivo generado supera el límite del evaluador.")
    return raw


def _write_new(path, data):
    with path.open("xb") as stream:
        if stream.write(data) != len(data):
            raise OSError("incomplete write")
        stream.flush()
        os.fsync(stream.fileno())


def _append(path, data):
    with path.open("ab") as stream:
        if stream.write(data) != len(data):
            raise OSError("incomplete write")
        stream.flush()
        os.fsync(stream.fileno())


def execute(attempts, output_path, event_path):
    records = [{"id": item["plan"]["id"], "scenario_id": item["plan"]["scenario_id"],
                "repetition": item["plan"]["repetition"], "kind": item["plan"]["kind"],
                "status": "not_started", "stage": None, "error": None, "capture_started": False,
                "capture_outcome": None, "output_saved": False, "api_cleanup": "not_created"} for item in attempts]
    output_integrity = event_integrity = "confirmed"
    for item, record in zip(attempts, records):
        server = thread = None
        record["status"], record["stage"] = "started", "api_start"
        try:
            _append(event_path, _encode({"id": record["id"], "event": "started"}) + b"\n")
        except OSError:
            record["status"], record["error"], event_integrity = "failed", "event_write_unconfirmed", "unconfirmed"
            break
        try:
            token = secrets.token_urlsafe(32)
            if any(token in _encode(value).decode("utf-8") for value in (item["case"], item["fixture"], item["documents"])):
                raise ValueError("synthetic inputs contain token")
            server, thread = _start_server(item["case"], item["fixture"], token)
            record["api_cleanup"] = "unconfirmed"
            record["stage"], record["capture_started"] = "capture", True
            result, event = capture(item["case"], token=token, port=server.server_port, limit=2, timeout_s=5)
            record["capture_outcome"] = event["outcome"]
            if event["outcome"] not in ("ok_answered", "ok_no_context"):
                raise ValueError("simulated capture failed")
            documents = {source["document_id"]: source["text"] for source in item["documents"]}
            expected = copy.deepcopy(item["fixture"])
            expected["id"] = item["case"]["id"]
            if not _sources_match(result, documents) or result != expected:
                raise ValueError("simulated response differs from fixture or corpus")
            raw = _jsonl([result])
            record["stage"] = "output_write"
            record["output_write_started"] = True
            _append(output_path, raw)
            record["output_saved"] = True
            record["output_line_sha256"] = hashlib.sha256(raw[:-1]).hexdigest()
            record["status"], record["stage"] = "completed", "completed"
        except KeyboardInterrupt:
            record["status"], record["error"] = "failed", "interrupted"
        except Exception:
            record["status"], record["error"] = "failed", "stage_failed"
        finally:
            if server is not None:
                try:
                    _stop_server(server, thread)
                    record["api_cleanup"] = "confirmed"
                except Exception:
                    record["api_cleanup"] = "unconfirmed"
        if record["api_cleanup"] == "unconfirmed":
            record["status"], record["error"] = "failed", "cleanup_unconfirmed"
        if record.get("output_write_started") and not record["output_saved"]:
            record["status"], record["error"], output_integrity = "failed", "output_write_unconfirmed", "unconfirmed"
        elif not record["output_saved"]:
            try:
                _append(output_path, _jsonl([_error(record["id"])]))
                record["output_saved"] = True
            except OSError:
                record["status"], record["error"], output_integrity = "failed", "output_write_unconfirmed", "unconfirmed"
        try:
            _append(event_path, _encode({"id": record["id"], "event": "finished", "status": record["status"],
                                         "capture_started": record["capture_started"], "error": record["error"]}) + b"\n")
        except OSError:
            record["status"], record["error"], event_integrity = "failed", "event_write_unconfirmed", "unconfirmed"
        if record["status"] != "completed":
            break
    return records, output_integrity, event_integrity


def run(cases_path, corpus_path, expectations_path, fixtures_path, output_dir):
    cases, corpus, expectations, fixtures, identities = load_inputs(cases_path, corpus_path, expectations_path, fixtures_path)
    attempts, generated_cases, generated_expectations = build_plan(cases, corpus, expectations, fixtures, identities)
    prepared = {"plan.jsonl": _jsonl([item["plan"] for item in attempts]), "cases.jsonl": _jsonl(generated_cases),
                "expectations.jsonl": _jsonl(generated_expectations), "outputs.jsonl": b"", "events.jsonl": b"",
                "attack_reviews.jsonl": b"", "control_reviews.jsonl": b""}
    folder = Path(output_dir)
    folder.mkdir()
    for filename, data in prepared.items():
        _write_new(folder / filename, data)
    records, output_integrity, event_integrity = execute(attempts, folder / "outputs.jsonl", folder / "events.jsonl")
    counts = {key: sum(record["status"] == key for record in records) for key in ("completed", "failed", "not_started")}
    report = {"schema_version": 1, "mode": "expanded_http_simulation", "planned": len(attempts),
              "attempted": sum(record["capture_started"] for record in records), **counts,
              "status": "completed" if counts["completed"] == len(attempts) else "incomplete",
              "output_integrity": output_integrity, "event_integrity": event_integrity,
              "input_sha256": {key: identities[key] for key in ("cases_sha256", "corpus_sha256", "expectations_sha256", "fixtures_sha256")},
              "artifact_sha256": {filename: hashlib.sha256((folder / filename).read_bytes()).hexdigest() for filename in prepared},
              "attempts": records}
    _write_new(folder / "report.json", _encode(report) + b"\n")
    return report


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise InputError("Argumentos inválidos; consulta --help.")


def main(argv=None):
    parser = _Parser(prog="evals_lab.security_expanded_simulation", description="Simula 24 intentos de seguridad.", allow_abbrev=False)
    for name in ("cases", "corpus", "expectations", "fixtures", "output-dir"):
        parser.add_argument("--" + name, required=True)
    try:
        args = parser.parse_args(argv)
        report = run(args.cases, args.corpus, args.expectations, args.fixtures, args.output_dir)
        summary = {key: report[key] for key in ("mode", "status", "planned", "attempted", "completed", "failed", "not_started")}
        raw = _encode(summary) + b"\n"
        if sys.stdout.buffer.write(raw) != len(raw):
            raise OSError("incomplete output")
        sys.stdout.buffer.flush()
        return 0 if report["status"] == "completed" else 1
    except InputError as exc:
        print(f"Error de simulación: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("Error de simulación: no se pudo completar la operación; revisa los archivos parciales.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
