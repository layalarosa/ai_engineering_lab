"""Lectura acotada y validación de archivos JSONL."""
import hashlib
import json
import re
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_LINE_BYTES = 65_536
MAX_RECORDS = 1000
MAX_TEXT_CHARS = 4000
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")


class InputError(ValueError):
    """Entrada inválida; sus mensajes no incluyen rutas ni contenido."""


def _object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_number(_value):
    # El contrato solo admite cadenas y así se evita procesar números enormes.
    raise ValueError("number is not allowed")


def _bounded_integer(value):
    # Índices de texto pequeños; evita convertir enteros arbitrariamente grandes.
    if len(value) > 10:
        raise ValueError("integer is too long")
    return int(value)


def _read_jsonl(path, *, kind, keys, allow_empty, id_field="id", validate=None,
                allow_integers=False, line_hashes=None, alternative_keys=()):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise InputError(f"No se pudo leer el archivo de {kind}.") from None
    if len(raw) > MAX_FILE_BYTES:
        raise InputError(f"El archivo de {kind} supera el límite de tamaño.")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise InputError(f"El archivo de {kind} no admite BOM.")
    digest = hashlib.sha256(raw).hexdigest()
    lines = raw.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    if not lines and not allow_empty:
        raise InputError(f"El archivo de {kind} está vacío.")
    if len(lines) > MAX_RECORDS:
        raise InputError(f"El archivo de {kind} supera el límite de registros.")
    records, seen = [], set()
    for index, line in enumerate(lines, 1):
        terminated = index < len(lines) or raw.endswith(b"\n")
        if line.endswith(b"\r"):
            if not terminated:
                raise InputError(f"Terminador inválido en {kind}, línea {index}.")
            line = line[:-1]
        if b"\r" in line or len(line) > MAX_LINE_BYTES or not line.strip():
            raise InputError(f"Línea inválida en {kind}, línea {index}.")
        try:
            record = json.loads(line.decode("utf-8"), object_pairs_hook=_object_without_duplicates,
                                parse_int=_bounded_integer if allow_integers else _reject_number,
                                parse_float=_reject_number, parse_constant=_reject_number)
            if not isinstance(record, dict) or (set(record) != keys and
                                                not any(set(record) == other for other in alternative_keys)):
                raise ValueError
            if validate is None:
                if not isinstance(record["id"], str) or ID_PATTERN.fullmatch(record["id"]) is None:
                    raise ValueError
                for key in keys - {"id"}:
                    value = record[key]
                    if not isinstance(value, str) or len(value) > MAX_TEXT_CHARS:
                        raise ValueError
                    value.encode("utf-8")
            else:
                validate(record)
        except (ValueError, UnicodeError, RecursionError):
            raise InputError(f"Registro inválido en {kind}, línea {index}.") from None
        if record[id_field] in seen:
            raise InputError(f"ID duplicado en {kind}, línea {index}.")
        seen.add(record[id_field])
        records.append(record)
        if line_hashes is not None:
            line_hashes[record[id_field]] = hashlib.sha256(line).hexdigest()
    return records, digest


def load_cases(path):
    return _read_jsonl(path, kind="casos", keys={"id", "input", "expected_output"}, allow_empty=False)


def load_outputs(path):
    return _read_jsonl(path, kind="respuestas", keys={"id", "output"}, allow_empty=True)
