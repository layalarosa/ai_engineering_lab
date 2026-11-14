"""Cliente mínimo para el endpoint de embeddings de Ollama."""

import json
import math
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

MAX_RESPONSE_BYTES = 16_000_000


class OllamaError(RuntimeError):
    """La solicitud o la respuesta de Ollama no pudo procesarse."""


def _validate_connection(base_url: str, timeout: float) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("la URL de Ollama debe ser HTTP(S), sin credenciales, consulta ni fragmento")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout debe ser positivo y finito")


def _read_json(response):
    data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise OllamaError("la respuesta de Ollama supera el límite permitido")
    return json.loads(data)


def generate(prompt: str, *, model: str, system: str, base_url: str = "http://localhost:11434", timeout: float = 120) -> str:
    """Solicita una respuesta completa al modelo local."""
    _validate_connection(base_url, timeout)
    if not model.strip() or not prompt.strip():
        raise ValueError("modelo, prompt o timeout inválido")
    body = json.dumps({"model": model, "prompt": prompt, "system": system, "stream": False}).encode("utf-8")
    request = Request(base_url.rstrip("/") + "/api/generate", data=body, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = _read_json(response)
    except HTTPError as exc:
        raise OllamaError(f"Ollama respondió con HTTP {exc.code}; comprueba el modelo y la entrada") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise OllamaError("No se pudo conectar con Ollama") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise OllamaError("Ollama devolvió JSON inválido") from exc
    if (not isinstance(payload, dict) or payload.get("done") is not True or
            not isinstance(payload.get("response"), str) or not payload["response"].strip()):
        raise OllamaError("Ollama devolvió una respuesta vacía o incompleta")
    return payload["response"].strip()


def embed(texts: list[str], *, model: str, base_url: str = "http://localhost:11434", timeout: float = 120) -> list[list[float]]:
    _validate_connection(base_url, timeout)
    if not model.strip():
        raise ValueError("model y timeout deben ser válidos")
    if not texts:
        return []
    data = json.dumps({"model": model, "input": texts, "truncate": False}).encode("utf-8")
    request = Request(base_url.rstrip("/") + "/api/embed", data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = _read_json(response)
    except HTTPError as exc:
        raise OllamaError(f"Ollama respondió con HTTP {exc.code}; comprueba el modelo y la entrada") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise OllamaError("No se pudo conectar con Ollama") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise OllamaError("Ollama devolvió JSON inválido") from exc
    vectors = payload.get("embeddings") if isinstance(payload, dict) else None
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise OllamaError("Número de embeddings inesperado")
    dimension = None
    for vector in vectors:
        if (not isinstance(vector, list) or not vector or
            any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector) or
            not any(vector) or (dimension is not None and len(vector) != dimension)):
            raise OllamaError("Embedding inválido o de dimensión inconsistente")
        dimension = len(vector)
    return vectors
