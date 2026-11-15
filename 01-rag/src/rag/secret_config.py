"""Carga explícita de secretos desde variables o archivos montados."""
import os
from pathlib import Path

MAX_SECRET_BYTES = 16384


def read_secret(name: str) -> str:
    """Acepta NAME o NAME_FILE; nunca incluye la ruta o el valor en errores."""
    value = os.environ.get(name)
    path = os.environ.get(name + "_FILE")
    if value is not None and path is not None:
        raise ValueError(f"Configura solo {name} o {name}_FILE")
    if path is None:
        return value or ""
    try:
        with Path(path).open("rb") as file:
            data = file.read(MAX_SECRET_BYTES + 1)
        if len(data) > MAX_SECRET_BYTES:
            raise ValueError("secreto demasiado grande")
        result = data.decode("utf-8").rstrip("\r\n")
        if not result:
            raise ValueError("secreto vacío")
        return result
    except (OSError, UnicodeError, ValueError):
        raise ValueError(f"No se pudo cargar {name}_FILE") from None
