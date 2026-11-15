"""Herramienta pura y su contrato visible para el cliente."""

import json

MAX_TEXT_CHARS = 4000

TOOL_DEFINITION = {
    "name": "text_stats",
    "description": "Cuenta puntos de código, bytes UTF-8 y palabras separadas por espacios en blanco.",
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string", "maxLength": MAX_TEXT_CHARS}},
        "required": ["text"],
        "additionalProperties": False,
    },
}


def text_stats(arguments):
    if not isinstance(arguments, dict) or set(arguments) != {"text"} or not isinstance(arguments["text"], str):
        raise ValueError("Se requiere únicamente el campo text de tipo cadena.")
    text = arguments["text"]
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("El texto supera 4000 puntos de código.")
    try:
        byte_count = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("El texto contiene caracteres Unicode no válidos.") from None
    return {"characters": len(text), "utf8_bytes": byte_count, "words": len(text.split())}


def call_text_stats(arguments):
    try:
        stats = text_stats(arguments)
        return {"content": [{"type": "text", "text": json.dumps(stats, ensure_ascii=True)}], "isError": False}
    except ValueError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
