"""Servidor stdio: una línea UTF-8 JSON por mensaje."""

import json
import sys

from .protocol import Session, error
from .supervisor import WorkerCleanupError

MAX_LINE_BYTES = 32768


def _reject_constant(value):
    raise ValueError("Constante JSON inválida")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Clave duplicada")
        result[key] = value
    return result


def serve(input_stream, output_stream, error_stream):
    session = Session()
    while True:
        line = input_stream.readline(MAX_LINE_BYTES + 1)
        if not line:
            return 0
        if len(line) > MAX_LINE_BYTES:
            print("Se cerró la sesión: mensaje demasiado grande.", file=error_stream, flush=True)
            return 1
        if not line.endswith(b"\n"):
            print("Se cerró la sesión: mensaje incompleto.", file=error_stream, flush=True)
            return 1
        try:
            message = json.loads(line.decode("utf-8"), parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, ValueError, RecursionError):
            response = error(None, -32700, "Parse error")
        else:
            try:
                response = session.handle(message)
            except WorkerCleanupError:
                print("Se cerró la sesión: no se pudo limpiar el proceso auxiliar.", file=error_stream, flush=True)
                return 1
        if response is not None:
            data = (json.dumps(response, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
            try:
                output_stream.write(data)
                output_stream.flush()
            except (BrokenPipeError, OSError):
                return 1


def main():
    return serve(sys.stdin.buffer, sys.stdout.buffer, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
