"""Captura un caso de seguridad desde una API local, sin juzgar su respuesta."""
import argparse
import hashlib
import http.client
import json
import math
import os
import socket
import sys

from .cases import InputError, MAX_LINE_BYTES, _read_jsonl
from .live_metrics import REQUEST_ID_PATTERN, TOKEN_PATTERN, _parse_json
from .security import _case, _output

MAX_CASES = 30
MAX_HTTP_BODY = 2_000_000


def _encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _error(case_id):
    return {"id": case_id, "status": "error", "answer": None, "sources": None, "sources_complete": None}


def _convert(raw, case_id, limit, token):
    body = _parse_json(raw)
    if not isinstance(body, dict) or set(body) != {"status", "answer", "sources"}:
        raise ValueError
    if (body["status"] not in ("answered", "no_context") or not isinstance(body["answer"], str) or
            not body["answer"].strip() or not isinstance(body["sources"], list) or len(body["sources"]) > limit):
        raise ValueError
    sources = []
    for index, source in enumerate(body["sources"], 1):
        if not isinstance(source, dict) or set(source) != {"label", "document_id", "chunk_index", "score", "text"}:
            raise ValueError
        if source["label"] != "[" + str(index) + "]" or type(source["score"]) not in (int, float) or not math.isfinite(source["score"]):
            raise ValueError
        sources.append({key: source[key] for key in ("document_id", "chunk_index", "text")})
    result = {"id": case_id, "status": body["status"], "answer": body["answer"],
              "sources": sources, "sources_complete": True}
    _output(result)
    encoded = _encode(result)
    if len(encoded) > MAX_LINE_BYTES or token.encode("ascii") in encoded:
        raise ValueError
    return result


def capture(case, *, token, port, limit, timeout_s):
    """Una llamada sin reintentos, con salida y evento de comunicación separados."""
    connection = None
    code = request_id = None
    result = _error(case["id"])
    outcome = "transport_error"
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout_s)
        data = _encode({"question": case["input"], "limit": limit})
        connection.request("POST", "/v1/answers", data,
                           {"Authorization": "Bearer " + token, "Content-Type": "application/json; charset=utf-8"})
        response = connection.getresponse()
        if type(response.status) is not int or not 100 <= response.status <= 599:
            raise http.client.HTTPException
        code = response.status
        ids = response.headers.get_all("X-Request-ID", [])
        if len(ids) == 1 and token not in ids[0] and REQUEST_ID_PATTERN.fullmatch(ids[0]) is not None:
            request_id = ids[0]
        lengths = response.headers.get_all("Content-Length", [])
        if (len(lengths) != 1 or response.headers.get_all("Transfer-Encoding", []) or
                not lengths[0].isascii() or not lengths[0].isdigit() or not 1 <= len(lengths[0]) <= 10):
            outcome = "framing_error"
        elif int(lengths[0]) > MAX_HTTP_BODY:
            outcome = "response_too_large"
        else:
            length = int(lengths[0])
            raw = response.read(length)
            if len(raw) != length:
                outcome = "transport_error"
            elif code != 200:
                outcome = "http_error"
            else:
                try:
                    result = _convert(raw, case["id"], limit, token)
                    outcome = "ok_" + result["status"]
                except (ValueError, UnicodeError, RecursionError, OverflowError):
                    outcome = "invalid_response"
    except (socket.timeout, TimeoutError):
        outcome = "timeout"
    except (http.client.IncompleteRead, http.client.RemoteDisconnected, OSError):
        outcome = "transport_error"
    except http.client.HTTPException:
        outcome = "framing_error"
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
    return result, {"outcome": outcome, "http_status": code, "request_id": request_id}


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise InputError("Argumentos inválidos; consulta --help.")


def main(argv=None):
    parser = _Parser(prog="evals_lab.security_capture", description="Captura un caso desde una API local.", allow_abbrev=False)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--timeout-s", type=int, default=180)
    try:
        args = parser.parse_args(argv)
        if not (1 <= args.port <= 65535 and 1 <= args.limit <= 20 and 1 <= args.timeout_s <= 180):
            raise InputError("Parámetros fuera de los límites permitidos.")
        cases, cases_hash = _read_jsonl(args.cases, kind="casos", keys={"id", "input", "kind", "attack_source", "criterion"},
                                        allow_empty=False, validate=_case, allow_integers=True)
        if len(cases) > MAX_CASES:
            raise InputError("El conjunto supera el límite de casos.")
        selected = next((case for case in cases if case["id"] == args.case_id), None)
        if selected is None:
            raise InputError("El caso seleccionado no existe.")
        token = os.environ.get("RAG_READ_TOKEN", "")
        if TOKEN_PATTERN.fullmatch(token) is None:
            raise InputError("Configura una credencial de lectura válida en el entorno.")
        if token in cases_hash or any(token in _encode(case).decode("utf-8") for case in cases):
            raise InputError("Las entradas no deben contener la credencial de lectura.")
        result, event = capture(selected, token=token, port=args.port, limit=args.limit, timeout_s=args.timeout_s)
        raw = _encode(result) + b"\n"
        line_hash = hashlib.sha256(raw[:-1]).hexdigest()
        if token in line_hash:
            raise InputError("No se pudo generar un informe sin exponer la credencial.")
        report = {"schema_version": 1, "evaluation_type": "security_capture", "cases_sha256": cases_hash,
                  "case_id": selected["id"], "output_line_sha256": line_hash,
                  "planned": 1, "attempted": 1, "not_started": 0,
                  "port": args.port, "limit": args.limit, "socket_timeout_s": args.timeout_s, **event}
        if sys.stdout.buffer.write(raw) != len(raw):
            raise OSError
        sys.stdout.buffer.flush()
        sys.stderr.write(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
        sys.stderr.flush()
        return 0 if event["outcome"] in ("ok_answered", "ok_no_context") else 1
    except (BrokenPipeError, OSError):
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(fd, sys.stdout.fileno())
            finally:
                os.close(fd)
        except OSError:
            pass
        print("Error de captura: no se pudo completar la operación.", file=sys.stderr)
        return 2
    except InputError as exc:
        print(f"Error de captura: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("Error de captura: no se pudo completar la operación.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
