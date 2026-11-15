"""Configuración opcional de la conexión local con RAG."""
import os
import re
from pathlib import Path

MAX_SECRET_BYTES = 16384


def rag_config(environ=None):
    env = os.environ if environ is None else environ
    url = env.get("MCP_RAG_BASE_URL", "")
    match = re.fullmatch(r"http://127\.0\.0\.1(?::([0-9]{1,5}))?/?", url)
    if not match:
        raise ValueError("Configuración de RAG inválida o ausente.")
    port = int(match.group(1) or "80")
    if not 1 <= port <= 65535:
        raise ValueError("Configuración de RAG inválida o ausente.")
    token = env.get("MCP_RAG_READ_TOKEN")
    path = env.get("MCP_RAG_READ_TOKEN_FILE")
    if token is not None and path is not None:
        raise ValueError("Configuración de RAG inválida o ausente.")
    if path is not None:
        try:
            with Path(path).open("rb") as file:
                data = file.read(MAX_SECRET_BYTES + 1)
            if len(data) > MAX_SECRET_BYTES:
                raise ValueError
            token = data.decode("utf-8").removesuffix("\n").removesuffix("\r")
        except (OSError, UnicodeError, ValueError):
            raise ValueError("Configuración de RAG inválida o ausente.") from None
    # El token debe poder transmitirse como valor seguro de una cabecera HTTP.
    if not token or len(token) > MAX_SECRET_BYTES or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("Configuración de RAG inválida o ausente.")
    return port, token
