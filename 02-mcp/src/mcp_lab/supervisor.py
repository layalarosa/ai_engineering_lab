"""Supervisa un único proceso auxiliar y sus canales binarios acotados."""
import json
import os
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import mcp_lab

from .rag_client import MAX_RESPONSE_BYTES, _reject_constant, _unique_object, _valid_text, tool_error
from .rag_worker import MAX_INPUT_BYTES

CLEANUP_SECONDS = 2
TIMEOUT_MESSAGE = "La consulta a RAG superó el tiempo máximo; el procesamiento remoto podría continuar."
WORKER_ERROR = "No se pudo completar la consulta a RAG."


class WorkerCleanupError(RuntimeError):
    """No se pudo confirmar la terminación segura del proceso auxiliar."""


def _worker_command():
    # En zipapp el importador apunta al archivo; en código fuente, al directorio src.
    archive = getattr(mcp_lab.__loader__, "archive", None)
    source = archive or str(Path(mcp_lab.__file__).resolve().parent.parent)
    script = "import sys; sys.path.insert(0, sys.argv[1]); from mcp_lab.rag_worker import main; raise SystemExit(main())"
    return [sys.executable, "-I", "-u", "-c", script, source]


def _worker_env():
    # El canal privado transporta la configuración; no heredar otros secretos.
    allowed = ("SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "LANG", "LC_ALL")
    return {name: os.environ[name] for name in allowed if name in os.environ}


def _read_exact(stream, length):
    chunks = []
    while length:
        data = stream.read(length)
        if not data:
            raise ValueError("Canal incompleto")
        chunks.append(data)
        length -= len(data)
    return b"".join(chunks)


def _valid_result(value):
    return (isinstance(value, dict) and set(value) == {"content", "isError"} and type(value["isError"]) is bool
            and isinstance(value["content"], list) and len(value["content"]) == 1
            and isinstance(value["content"][0], dict) and set(value["content"][0]) == {"type", "text"}
            and value["content"][0]["type"] == "text" and _valid_text(value["content"][0]["text"]))


def _cleanup(process, threads, terminate):
    cleanup_deadline = time.monotonic() + CLEANUP_SECONDS
    if terminate and process.poll() is None:
        try:
            process.kill()
        except OSError:
            if process.poll() is None:
                raise WorkerCleanupError from None
    try:
        process.wait(timeout=max(0, cleanup_deadline - time.monotonic()))
    except (subprocess.TimeoutExpired, OSError):
        raise WorkerCleanupError from None
    for thread in threads:
        thread.join(timeout=max(0, cleanup_deadline - time.monotonic()))
    if any(thread.is_alive() for thread in threads):
        raise WorkerCleanupError
    for pipe in (process.stdin, process.stdout):
        if pipe is not None and not pipe.closed:
            try:
                pipe.close()
            except OSError:
                raise WorkerCleanupError from None


def run_rag_worker(arguments, config, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return tool_error(TIMEOUT_MESSAGE)
    try:
        process = subprocess.Popen(_worker_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=_worker_env(), close_fds=True,
                                   bufsize=0, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, ValueError):
        return tool_error(WORKER_ERROR)
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _cleanup(process, [], True)
            return tool_error(TIMEOUT_MESSAGE)
        value = {"arguments": arguments, "config": config, "remaining": min(remaining, 600)}
        data = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
        if len(data) > MAX_INPUT_BYTES:
            _cleanup(process, [], True)
            return tool_error("La configuración de RAG supera el tamaño permitido.")
        payload = struct.pack("!I", len(data)) + data
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _cleanup(process, [], True)
        return tool_error(WORKER_ERROR)

    changed = threading.Event()
    state = {"writer_done": False, "reader_done": False, "error": False, "result": None}

    def write_input():
        try:
            view = memoryview(payload)
            while view:
                count = process.stdin.write(view)
                if not count:
                    raise OSError("Canal cerrado")
                view = view[count:]
        except (OSError, ValueError):
            state["error"] = True
        finally:
            try:
                process.stdin.close()
            except OSError:
                state["error"] = True
            state["writer_done"] = True
            changed.set()

    def read_output():
        try:
            size = struct.unpack("!I", _read_exact(process.stdout, 4))[0]
            if size > MAX_RESPONSE_BYTES:
                raise ValueError("Respuesta demasiado grande")
            output = _read_exact(process.stdout, size)
            if process.stdout.read(1):
                raise ValueError("Datos adicionales")
            value = json.loads(output.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            if not _valid_result(value):
                raise ValueError("Respuesta inválida")
            state["result"] = value
        except (OSError, ValueError, UnicodeError, RecursionError):
            state["error"] = True
        finally:
            state["reader_done"] = True
            changed.set()

    writer = threading.Thread(target=write_input, daemon=True)
    reader = threading.Thread(target=read_output, daemon=True)
    threads = []
    try:
        writer.start()
        threads.append(writer)
        reader.start()
        threads.append(reader)
    except (OSError, RuntimeError):
        _cleanup(process, threads, True)
        return tool_error(WORKER_ERROR)
    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                _cleanup(process, threads, True)
                return tool_error(TIMEOUT_MESSAGE)
            if state["error"]:
                _cleanup(process, threads, True)
                return tool_error(WORKER_ERROR)
            if state["reader_done"] and state["writer_done"] and process.poll() is not None:
                if process.returncode != 0:
                    _cleanup(process, threads, False)
                    return tool_error(WORKER_ERROR)
                result = state["result"]
                _cleanup(process, threads, False)
                # La limpieza y la validación también consumen el plazo.
                return tool_error(TIMEOUT_MESSAGE) if time.monotonic() >= deadline else result
            changed.wait(min(0.05, deadline - now))
            changed.clear()
    except BaseException:
        if process.poll() is None:
            _cleanup(process, threads, True)
        raise
