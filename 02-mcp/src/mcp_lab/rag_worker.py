"""Proceso interno: procesa una consulta y responde por un canal privado."""
import json
import math
import os
import struct
import sys
import threading

from .rag_client import MAX_RESPONSE_BYTES, _call_ask_rag_direct, tool_error

MAX_INPUT_BYTES = 256 * 1024
MAX_WORKER_LIFETIME_SECONDS = 605


def _read_exact(stream, size):
    chunks = []
    while size:
        chunk = stream.read(size)
        if not chunk:
            raise ValueError("Canal incompleto")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def main():
    # Defensa de mejor esfuerzo si el supervisor desaparece antes de enviar datos.
    watchdog = threading.Timer(MAX_WORKER_LIFETIME_SECONDS, os._exit, args=(124,))
    watchdog.daemon = True
    watchdog.start()
    try:
        source = sys.stdin.buffer
        length = struct.unpack("!I", _read_exact(source, 4))[0]
        if length > MAX_INPUT_BYTES:
            raise ValueError("Entrada demasiado grande")
        request = json.loads(_read_exact(source, length).decode("utf-8"))
        if source.read(1):
            raise ValueError("Datos adicionales")
        remaining = request["remaining"]
        if type(remaining) not in (float, int) or not math.isfinite(remaining) or not 0 < remaining <= 600:
            raise ValueError("Plazo inválido")
        config = request["config"]
        if not isinstance(config, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in config.items()):
            raise ValueError("Configuración inválida")
        watchdog.cancel()
        watchdog = threading.Timer(remaining + 2, os._exit, args=(124,))
        watchdog.daemon = True
        watchdog.start()
        value = _call_ask_rag_direct(request["arguments"], config)
        data = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
        if len(data) > MAX_RESPONSE_BYTES:
            value = tool_error("La respuesta MCP supera el tamaño permitido.")
            data = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        sys.stdout.buffer.write(struct.pack("!I", len(data)) + data)
        sys.stdout.buffer.flush()
    except (ValueError, KeyError, TypeError, OSError, UnicodeError, RecursionError):
        # No incluir argumentos, ruta de secretos, causa ni trazas en los canales.
        return 1
    finally:
        watchdog.cancel()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
