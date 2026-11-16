"""Prototipo didáctico de selección con metadatos explícitos de origen."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from memory_lab.memory import ConversationMemory


def _key(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Clave inválida")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Clave inválida") from None
    return value


def _validate(messages, records, intention, max_chars, max_messages):
    if not isinstance(messages, list) or len(messages) > 1000:
        raise ValueError("Historial inválido")
    memory = ConversationMemory()
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise ValueError("Mensaje inválido")
        memory.append(message["role"], message["content"])
    # Un historial vacío permite validar límites sin aplicar la regla de recencia.
    ConversationMemory().context(max_chars=max_chars, max_messages=max_messages)
    if not isinstance(intention, dict):
        raise ValueError("Intención inválida")
    mode = intention.get("modo")
    if mode == "actual" and set(intention) == {"modo", "clave"}:
        _key(intention["clave"])
    elif mode != "no_soportado" or set(intention) != {"modo"}:
        raise ValueError("Intención inválida")
    if not isinstance(records, list) or len(records) > len(messages):
        raise ValueError("Registros inválidos")
    by_position = {}
    for record in records:
        if not isinstance(record, dict) or set(record) not in (
                {"posicion", "clave"}, {"posicion", "clave", "sustituye_posicion"}):
            raise ValueError("Registro inválido")
        position = record["posicion"]
        if type(position) is not int or not 1 <= position <= len(messages) or position in by_position:
            raise ValueError("Posición inválida")
        _key(record["clave"])
        by_position[position] = record
    for position, record in by_position.items():
        if "sustituye_posicion" not in record:
            continue
        previous = record["sustituye_posicion"]
        if (type(previous) is not int or previous not in by_position or previous >= position
                or by_position[previous]["clave"] != record["clave"]):
            raise ValueError("Referencia inválida")
    return by_position


def select(messages, records, intention, max_chars=4000, max_messages=20):
    """Entrega una copia del mensaje original o un estado explícito sin evidencia."""
    try:
        by_position = _validate(messages, records, intention, max_chars, max_messages)
    except ValueError:
        return {"estado": "entrada_invalida", "posiciones": [], "mensajes": []}

    def empty(state):
        return {"estado": state, "posiciones": [], "mensajes": []}

    if intention["modo"] == "no_soportado":
        return empty("intencion_no_soportada")
    same_key = {position: record for position, record in by_position.items()
                if record["clave"] == intention["clave"]}
    if not same_key:
        return empty("sin_evidencia_registrada")
    replaced = {record["sustituye_posicion"] for record in same_key.values()
                if "sustituye_posicion" in record}
    leaves = set(same_key) - replaced
    if len(leaves) != 1:
        return empty("evidencia_ambigua")
    position = next(iter(leaves))
    message = messages[position - 1]
    if len(message["content"]) > max_chars:
        return empty("presupuesto_insuficiente")
    return {"estado": "seleccionado", "posiciones": [position], "mensajes": [dict(message)]}
