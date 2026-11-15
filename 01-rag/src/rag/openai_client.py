"""Cliente HTTP mínimo de embeddings y Responses de OpenAI."""
import json
import math
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .secret_config import read_secret

MAX_RESPONSE_BYTES = 16_000_000


class OpenAIError(RuntimeError):
    """Fallo de comunicación o respuesta inválida de OpenAI."""


def _post(endpoint, body):
    try:
        key = read_secret("OPENAI_API_KEY").strip()
    except ValueError as exc:
        raise OpenAIError("No se pudo cargar la clave de OpenAI") from exc
    if not key:
        raise OpenAIError("Configura OPENAI_API_KEY para usar OpenAI")
    request = Request("https://api.openai.com/v1/" + endpoint, data=json.dumps(body).encode("utf-8"),
                      headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=120) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise OpenAIError("La respuesta de OpenAI supera el límite permitido")
        return json.loads(data)
    except HTTPError as exc:
        raise OpenAIError(f"OpenAI respondió con HTTP {exc.code}; comprueba acceso y configuración") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise OpenAIError("No se pudo conectar con OpenAI") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise OpenAIError("OpenAI devolvió JSON inválido") from exc


def embed(texts, *, model, base_url=None):
    if not model.strip():
        raise ValueError("modelo obligatorio")
    if not texts:
        return []
    payload = _post("embeddings", {"model": model, "input": texts, "encoding_format": "float"})
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or len(data) != len(texts):
        raise OpenAIError("Número de embeddings inesperado")
    vectors = [None] * len(texts)
    for item in data:
        if not isinstance(item, dict) or type(item.get("index")) is not int or not 0 <= item["index"] < len(texts) or vectors[item["index"]] is not None:
            raise OpenAIError("Índice de embedding inválido")
        vector = item.get("embedding")
        if not isinstance(vector, list) or not vector or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector) or not any(vector):
            raise OpenAIError("Embedding inválido")
        vectors[item["index"]] = vector
    if len({len(v) for v in vectors}) != 1:
        raise OpenAIError("Dimensiones inconsistentes")
    return vectors


def generate(prompt, *, model, system, base_url=None):
    if not model.strip() or not prompt.strip():
        raise ValueError("modelo y prompt son obligatorios")
    payload = _post("responses", {"model": model, "instructions": system, "input": prompt, "store": False})
    if not isinstance(payload, dict) or payload.get("status") != "completed" or not isinstance(payload.get("output"), list):
        raise OpenAIError("Respuesta de OpenAI incompleta o inválida")
    parts = []
    for item in payload["output"]:
        if isinstance(item, dict) and item.get("type") == "message" and isinstance(item.get("content"), list):
            for content in item["content"]:
                if isinstance(content, dict) and content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    parts.append(content["text"])
    text = "\n".join(parts).strip()
    if not text:
        raise OpenAIError("OpenAI no devolvió texto de respuesta")
    return text
