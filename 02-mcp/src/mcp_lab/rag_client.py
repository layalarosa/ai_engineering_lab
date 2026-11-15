"""Consulta explícita a la API local RAG, sin redirecciones ni proxies."""
import http.client
import json
import math
import os
import socket
import time

from .config import rag_config

HTTP_TIMEOUT_SECONDS = 270
MAX_RESPONSE_BYTES = 256 * 1024
DEFAULT_TOTAL_TIMEOUT_SECONDS = 270
MAX_TOTAL_TIMEOUT_SECONDS = 600

TOOL_DEFINITION = {
    "name": "ask_rag",
    "description": "Consulta el corpus RAG configurado y devuelve respuesta y fuentes; puede invocar un modelo y generar costes.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "minLength": 1, "maxLength": 4000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 3},
        },
        "required": ["question"],
        "additionalProperties": False,
    },
}


def tool_error(message):
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _arguments(arguments):
    if set(arguments) - {"question", "limit"}:
        raise ValueError("Argumentos de consulta inválidos.")
    question, limit = arguments.get("question"), arguments.get("limit", 3)
    if not isinstance(question, str) or not question.strip() or len(question) > 4000 or type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Argumentos de consulta inválidos.")
    try:
        question.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Argumentos de consulta inválidos.") from None
    return question, limit


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _valid_text(value):
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _validate_response(value):
    if not isinstance(value, dict) or value.get("status") not in ("answered", "no_context") or not _valid_text(value.get("answer")):
        raise ValueError
    sources = value.get("sources")
    if not isinstance(sources, list) or len(sources) > 20 or (value["status"] == "no_context" and sources) or (value["status"] == "answered" and not sources):
        raise ValueError
    clean = []
    for source in sources:
        if not isinstance(source, dict) or not all(_valid_text(source.get(k)) for k in ("label", "document_id", "text")):
            raise ValueError
        index, score = source.get("chunk_index"), source.get("score")
        if type(index) is not int or index < 0 or type(score) not in (int, float):
            raise ValueError
        try:
            if not math.isfinite(score):
                raise ValueError
        except OverflowError:
            raise ValueError from None
        clean.append({k: source[k] for k in ("label", "document_id", "chunk_index", "score", "text")})
    return {"status": value["status"], "answer": value["answer"], "sources": clean}


def _reject_constant(_value):
    raise ValueError


def _call_ask_rag_direct(arguments, config=None):
    try:
        question, limit = _arguments(arguments)
        port, token = rag_config(config)
    except ValueError as exc:
        return tool_error(str(exc))

    # UTF-8 evita expandir cada carácter fuera del BMP en dos escapes JSON.
    body = json.dumps({"question": question, "limit": limit}, ensure_ascii=False).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=HTTP_TIMEOUT_SECONDS)
    try:
        connection.request("POST", "/v1/answers", body=body, headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json; charset=utf-8", "Accept": "application/json",
        })
        response = connection.getresponse()
        if response.status in (401, 403):
            return tool_error("No se pudo autenticar o autorizar la consulta a RAG.")
        if response.status == 429:
            return tool_error("RAG está ocupado; se puede reintentar manualmente.")
        if response.status in (502, 503, 504):
            return tool_error("RAG o una de sus dependencias no está disponible o agotó el tiempo de espera.")
        if response.status != 200:
            return tool_error("RAG rechazó la consulta o devolvió un estado inesperado.")
        if response.headers.get_content_type() != "application/json":
            return tool_error("La respuesta de RAG no es JSON válido.")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            return tool_error("La respuesta de RAG supera el tamaño permitido.")
        if response.length not in (None, 0):
            return tool_error("La respuesta de RAG no está completa.")
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        clean = _validate_response(value)
        text = json.dumps(clean, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except (socket.timeout, TimeoutError):
        return tool_error("La consulta a RAG agotó el tiempo de espera.")
    except (OSError, http.client.HTTPException):
        return tool_error("No se pudo conectar con RAG o se interrumpió la respuesta.")
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        return tool_error("La respuesta de RAG no es válida.")
    finally:
        connection.close()


def call_ask_rag(arguments):
    """Ejecuta la consulta en un proceso supervisado por un plazo total."""
    started = time.monotonic()
    try:
        _arguments(arguments)
    except ValueError as exc:
        return tool_error(str(exc))
    configured = os.environ.get("MCP_RAG_TOTAL_TIMEOUT_SECONDS")
    if configured is None:
        seconds = DEFAULT_TOTAL_TIMEOUT_SECONDS
    elif (not configured.isascii() or not configured.isdigit() or len(configured) > 3
          or not 1 <= int(configured) <= MAX_TOTAL_TIMEOUT_SECONDS):
        return tool_error("El plazo total de RAG es inválido.")
    else:
        seconds = int(configured)
    config = {name: os.environ[name] for name in
              ("MCP_RAG_BASE_URL", "MCP_RAG_READ_TOKEN", "MCP_RAG_READ_TOKEN_FILE") if name in os.environ}
    from .supervisor import run_rag_worker
    return run_rag_worker(arguments, config, started + seconds)
