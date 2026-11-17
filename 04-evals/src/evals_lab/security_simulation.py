"""Captura salidas sintéticas de un servidor HTTP simulado y efímero."""
import argparse
import hmac
import http.client
import json
import os
import secrets
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from .cases import InputError, MAX_FILE_BYTES, MAX_LINE_BYTES, _read_jsonl
from .live_metrics import _parse_json
from .security import _case, _output

MAX_CASES = 30
MAX_HTTP_BODY = 262_144
TIMEOUT_S = 5


class _LocalServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(TIMEOUT_S)
        return connection, address


def _encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def load_simulation(cases_path, fixtures_path):
    cases, _ = _read_jsonl(cases_path, kind="casos", keys={"id", "input", "kind", "attack_source", "criterion"},
                            allow_empty=False, validate=_case, allow_integers=True)
    fixtures, _ = _read_jsonl(fixtures_path, kind="respuestas simuladas",
                               keys={"id", "status", "answer", "sources", "sources_complete"},
                               allow_empty=False, validate=_output, allow_integers=True)
    if len(cases) > MAX_CASES or [r["id"] for r in cases] != [r["id"] for r in fixtures]:
        raise InputError("Los casos y las respuestas simuladas deben coincidir en orden y cantidad; máximo 30.")
    if any(r["status"] != "error" and not r["sources_complete"] for r in fixtures):
        raise InputError("Las respuestas simuladas válidas deben contener todas sus fuentes.")
    return cases, fixtures


def _mock_handler(cases, fixtures, token):
    class Handler(BaseHTTPRequestHandler):
        index = 0
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if (self.path != "/v1/answers" or not 0 <= length <= MAX_HTTP_BODY or
                        not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token)):
                    self._send(400, {})
                    return
                request = _parse_json(self.rfile.read(length))
                index = type(self).index
                if index >= len(cases) or request != {"question": cases[index]["input"], "limit": 20}:
                    self._send(400, {})
                    return
                type(self).index += 1
                fixture = fixtures[index]
                if fixture["status"] == "error":
                    self._send(500, {"error": "simulated"})
                    return
                sources = [{"label": "[" + str(i) + "]", **source, "score": 0.5}
                           for i, source in enumerate(fixture["sources"], 1)]
                self._send(200, {"status": fixture["status"], "answer": fixture["answer"], "sources": sources})
            except (ValueError, UnicodeError, OSError, RecursionError, OverflowError):
                self.close_connection = True

        def _send(self, status, body):
            raw = _encode(body)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return Handler


def _error(case_id):
    return {"id": case_id, "status": "error", "answer": None, "sources": None, "sources_complete": None}


def capture_one(case, *, port, token):
    """Lee una respuesta completa; un fallo incierto detiene el lote."""
    connection = None
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT_S)
        body = _encode({"question": case["input"], "limit": 20})
        connection.request("POST", "/v1/answers", body,
                           {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        response = connection.getresponse()
        lengths = response.headers.get_all("Content-Length", [])
        if (len(lengths) != 1 or response.headers.get_all("Transfer-Encoding", []) or
                not lengths[0].isascii() or not lengths[0].isdigit() or len(lengths[0]) > 8):
            return _error(case["id"]), True
        length = int(lengths[0])
        if length > MAX_HTTP_BODY:
            return _error(case["id"]), True
        raw = response.read(length)
        if len(raw) != length:
            return _error(case["id"]), True
        if response.status != 200:
            return _error(case["id"]), False
        data = _parse_json(raw)
        if not isinstance(data, dict) or set(data) != {"status", "answer", "sources"} or not isinstance(data["sources"], list):
            raise ValueError
        sources = []
        for source in data["sources"]:
            if not isinstance(source, dict) or set(source) != {"label", "document_id", "chunk_index", "score", "text"}:
                raise ValueError
            sources.append({key: source[key] for key in ("document_id", "chunk_index", "text")})
        result = {"id": case["id"], "status": data["status"], "answer": data["answer"],
                  "sources": sources, "sources_complete": True}
        _output(result)
        if result["status"] == "error" or token.encode("ascii") in _encode(result):
            raise ValueError
        if len(_encode(result)) > MAX_LINE_BYTES:
            raise ValueError
        return result, False
    except (socket.timeout, TimeoutError, OSError, http.client.HTTPException):
        return _error(case["id"]), True
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        return _error(case["id"]), False
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass


def simulate(cases, fixtures):
    """Crea el servidor propio; no acepta destinos configurables ni reintenta."""
    token = secrets.token_urlsafe(32)
    if any(token in _encode(row).decode("utf-8") for row in cases + fixtures):
        raise InputError("Las entradas no deben contener la credencial de la simulación.")
    server = _LocalServer(("127.0.0.1", 0), _mock_handler(cases, fixtures, token))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    results = []
    try:
        thread.start()
        for case in cases:
            row, stop = capture_one(case, port=server.server_port, token=token)
            results.append(row)
            if stop:
                break
    finally:
        if thread.is_alive():
            server.shutdown()
            thread.join()
        server.server_close()
    return results


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise InputError("Argumentos inválidos; usa --cases y --fixtures.")


def main(argv=None):
    parser = _Parser(prog="evals_lab.security_simulation", description="Captura respuestas de una API simulada local.", allow_abbrev=False)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--fixtures", required=True)
    try:
        args = parser.parse_args(argv)
        cases, fixtures = load_simulation(args.cases, args.fixtures)
        results = simulate(cases, fixtures)
        raw = b"".join(_encode(row) + b"\n" for row in results)
        if len(raw) > MAX_FILE_BYTES:
            raise InputError("Las respuestas capturadas superan el límite del evaluador.")
        if sys.stdout.buffer.write(raw) != len(raw):
            raise OSError
        sys.stdout.buffer.flush()
        return 1 if len(results) != len(cases) or any(r["status"] == "error" for r in results) else 0
    except (BrokenPipeError, OSError):
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(fd, sys.stdout.fileno())
            finally:
                os.close(fd)
        except OSError:
            pass
        print("Error de simulación: no se pudo completar la operación.", file=sys.stderr)
        return 2
    except InputError as exc:
        print(f"Error de simulación: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("Error de simulación: no se pudo completar la operación.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
