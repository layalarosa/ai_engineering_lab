"""Mide llamadas secuenciales a una API RAG local sin conservar respuestas."""
import argparse
import hashlib
import http.client
import json
import math
import os
import re
import socket
import sys
import time
from pathlib import Path

from .cases import InputError, _object_without_duplicates, _read_jsonl
from .grounding import _id, _text
from .rag_adapter import _bounded_int, _finite_float, _reject_constant
from .retrieval import DOCUMENT_ID

MAX_CONTEXT_BYTES = 65_536
MAX_BODY_BYTES = 2_000_000
MAX_ATTEMPTS = 30
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
REQUEST_ID_PATTERN = re.compile(r"[a-f0-9]{32}\Z")
CONTEXT_KEYS = {"schema_version", "embedding_provider", "embedding_model", "generation_provider",
                "generation_model", "corpus_revision", "server_revision", "hardware_note", "min_score"}
OUTCOMES = ("ok_answered", "ok_no_context", "http_error", "timeout", "transport_error",
            "framing_error", "invalid_response", "response_too_large")
SUCCESS = ("ok_answered", "ok_no_context")
STOP_OUTCOMES = ("timeout", "transport_error", "framing_error", "response_too_large")
STOP_HTTP = (401, 403, 429, 503, 504)


def _validate_case(record):
    _id(record["id"])
    _text(record["input"], nonblank=True)


def _parse_json(raw):
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_object_without_duplicates,
                      parse_int=_bounded_int, parse_float=_finite_float, parse_constant=_reject_constant)


def load_context(path):
    """Lee metadatos declarados, sin interpretar sus valores como configuración real."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_CONTEXT_BYTES + 1)
        if len(raw) > MAX_CONTEXT_BYTES:
            raise ValueError
        context = _parse_json(raw)
        if not isinstance(context, dict) or set(context) != CONTEXT_KEYS:
            raise ValueError
        if type(context["schema_version"]) is not int or context["schema_version"] != 1:
            raise ValueError
        for key in CONTEXT_KEYS - {"schema_version", "min_score"}:
            value = context[key]
            if not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.isprintable():
                raise ValueError
            value.encode("utf-8")
        if context["embedding_provider"] not in ("ollama", "simulated", "unknown") or context["generation_provider"] not in ("ollama", "simulated", "unknown"):
            raise ValueError
        score = context["min_score"]
        if score is not None and score != "unknown":
            if type(score) not in (int, float) or not math.isfinite(score) or not -1 <= score <= 1:
                raise ValueError
        return context, hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise InputError("El contexto no es válido o no se pudo leer.") from None


def _valid_response(raw, limit):
    try:
        body = _parse_json(raw)
        if not isinstance(body, dict):
            raise ValueError
        status, answer, sources = body.get("status"), body.get("answer"), body.get("sources")
        if (status not in ("answered", "no_context") or not isinstance(answer, str) or not answer.strip() or
                not isinstance(sources, list) or len(sources) > limit or
                (status == "no_context") != (len(sources) == 0)):
            raise ValueError
        answer.encode("utf-8")
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError
            _id(source.get("document_id"), DOCUMENT_ID)
        return "ok_answered" if status == "answered" else "ok_no_context"
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        return "invalid_response"


def perform_attempt(body, token, *, port, timeout_s, limit):
    """Mide una llamada; los argumentos de solicitud ya están preparados."""
    start = time.monotonic_ns()
    connection = None
    status = request_id = None
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout_s)
        connection.request("POST", "/v1/answers", body,
                           {"Authorization": "Bearer " + token, "Content-Type": "application/json; charset=utf-8"})
        response = connection.getresponse()
        if type(response.status) is not int or not 100 <= response.status <= 599:
            outcome = "framing_error"
        else:
            status = response.status
            ids = response.headers.get_all("X-Request-ID", [])
            if len(ids) == 1 and ids[0] != token and REQUEST_ID_PATTERN.fullmatch(ids[0]) is not None:
                request_id = ids[0]
            lengths = response.headers.get_all("Content-Length", [])
            transfers = response.headers.get_all("Transfer-Encoding", [])
            if (len(lengths) != 1 or transfers or not lengths[0].isascii() or not lengths[0].isdigit() or
                    not 1 <= len(lengths[0]) <= 10):
                outcome = "framing_error"
            else:
                length = int(lengths[0])
                if length > MAX_BODY_BYTES:
                    outcome = "response_too_large"
                else:
                    raw = response.read(length)
                    if len(raw) != length:
                        outcome = "transport_error"
                    elif status != 200:
                        outcome = "http_error"
                    else:
                        outcome = _valid_response(raw, limit)
    except (socket.timeout, TimeoutError):
        outcome = "timeout"
    except (http.client.IncompleteRead, http.client.RemoteDisconnected, OSError):
        outcome = "transport_error"
    except http.client.HTTPException:
        outcome = "framing_error"
    finally:
        duration_ns = time.monotonic_ns() - start
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
    return {"outcome": outcome, "duration_ns": duration_ns, "http_status": status, "request_id": request_id}


def _distribution(durations):
    ordered = sorted(durations)
    n = len(ordered)
    return {"count": n, "min_ns": ordered[0] if n else None, "max_ns": ordered[-1] if n else None,
            "p50_ns": ordered[(n + 1) // 2 - 1] if n else None,
            "p95_ns": ordered[(95 * n + 99) // 100 - 1] if n >= 20 else None}


def _phase(planned, records):
    return {"planned": planned, "attempted": len(records), "not_started": planned - len(records), "results": records}


def _stop_reason(event):
    if event["outcome"] in STOP_OUTCOMES:
        return event["outcome"]
    if event["outcome"] == "http_error" and event["http_status"] in STOP_HTTP:
        return "http_" + str(event["http_status"])
    return None


def run(cases, *, context, cases_sha256, context_sha256, token, port, limit, repeats, warmup, timeout_s):
    """Ejecuta un lote previamente validado; no reintenta ni guarda cuerpos."""
    bodies = {case["id"]: json.dumps({"question": case["input"], "limit": limit},
                                      ensure_ascii=True, separators=(",", ":")).encode("ascii") for case in cases}
    warmup_results, measured_results = [], []
    run_status, abort_reason = "completed", None
    for index in range(1, warmup + 1):
        case = cases[0]
        event = perform_attempt(bodies[case["id"]], token, port=port, timeout_s=timeout_s, limit=limit)
        event.update({"phase": "warmup", "case_id": case["id"], "repetition": index})
        warmup_results.append(event)
        if event["outcome"] not in SUCCESS:
            run_status, abort_reason = "aborted_warmup", _stop_reason(event) or event["outcome"]
            break
    if run_status == "completed":
        for repetition in range(1, repeats + 1):
            for case in cases:
                event = perform_attempt(bodies[case["id"]], token, port=port, timeout_s=timeout_s, limit=limit)
                event.update({"phase": "measurement", "case_id": case["id"], "repetition": repetition})
                measured_results.append(event)
                reason = _stop_reason(event)
                if reason:
                    run_status, abort_reason = "aborted_policy", reason
                    break
            if run_status != "completed":
                break
    counts = {key: sum(event["outcome"] == key for event in measured_results) for key in OUTCOMES}
    attempted = len(measured_results)
    failures = attempted - counts["ok_answered"] - counts["ok_no_context"]
    measurement = _phase(len(cases) * repeats, measured_results)
    measurement.update({"outcome_counts": counts, "failure_rate_observed": failures / attempted if attempted else None,
                        "latency_ns": _distribution([event["duration_ns"] for event in measured_results if event["outcome"] in SUCCESS]),
                        "answered_latency_ns": _distribution([event["duration_ns"] for event in measured_results if event["outcome"] == "ok_answered"]),
                        "no_context_latency_ns": _distribution([event["duration_ns"] for event in measured_results if event["outcome"] == "ok_no_context"])})
    return {"schema_version": 1, "evaluation_type": "live_metrics", "cases_sha256": cases_sha256,
            "context_sha256": context_sha256, "context": context,
            "config": {"port": port, "limit": limit, "socket_timeout_s": timeout_s, "repeats": repeats, "warmup": warmup},
            "run_status": run_status, "abort_reason": abort_reason,
            "warmup": _phase(warmup, warmup_results), "measurement": measurement}


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise InputError("Argumentos inválidos; consulta --help.")


def main(argv=None):
    parser = _Parser(prog="evals_lab.live_metrics", description="Mide una API RAG local de forma secuencial.",
                     allow_abbrev=False)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--timeout-s", type=int, default=180)
    try:
        args = parser.parse_args(argv)
        if not (1 <= args.port <= 65535 and 1 <= args.limit <= 20 and 1 <= args.repeats <= 5 and
                0 <= args.warmup <= 2 and 1 <= args.timeout_s <= 180):
            raise InputError("Parámetros fuera de los límites permitidos.")
        cases, cases_hash = _read_jsonl(args.cases, kind="casos", keys={"id", "input"},
                                        allow_empty=False, validate=_validate_case)
        if len(cases) > MAX_ATTEMPTS or len(cases) * args.repeats > MAX_ATTEMPTS:
            raise InputError("El lote supera el límite de intentos.")
        context, context_hash = load_context(args.context)
        token = os.environ.get("RAG_READ_TOKEN", "")
        if TOKEN_PATTERN.fullmatch(token) is None:
            raise InputError("Configura una credencial de lectura válida en el entorno.")
        if (token in cases_hash or token in context_hash or
                any(token in value for value in context.values() if isinstance(value, str)) or
                any(token in case["id"] for case in cases)):
            raise InputError("Las entradas no deben exponer la credencial de lectura.")
        report = run(cases, context=context, cases_sha256=cases_hash, context_sha256=context_hash,
                     token=token, port=args.port, limit=args.limit, repeats=args.repeats,
                     warmup=args.warmup, timeout_s=args.timeout_s)
        encoded = (json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                              allow_nan=False) + "\n").encode("ascii")
        if sys.stdout.buffer.write(encoded) != len(encoded):
            raise OSError("incomplete output")
        sys.stdout.buffer.flush()
        return 0 if report["run_status"] == "completed" and report["measurement"]["failure_rate_observed"] == 0 else 1
    except OSError:
        try:
            null_fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(null_fd, sys.stdout.fileno())
            finally:
                os.close(null_fd)
        except OSError:
            pass
        print("Error de medición: no se pudo escribir el informe.", file=sys.stderr)
        return 2
    except InputError as exc:
        print(f"Error de medición: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Error de medición: ejecución interrumpida.", file=sys.stderr)
        return 2
    except Exception:
        print("Error de medición: no se pudo completar la medición.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
