"""Historial en memoria y selección determinista de mensajes recientes."""
from dataclasses import dataclass

MAX_MESSAGE_CHARS = 4000
MAX_HISTORY_MESSAGES = 1000
MAX_CONTEXT_CHARS = 20000
MAX_CONTEXT_MESSAGES = 100


@dataclass(frozen=True, slots=True)
class Message:
    role: str
    content: str


def _validate_message(role: str, content: str) -> Message:
    if not isinstance(role, str) or role not in ("user", "assistant"):
        raise ValueError("El rol debe ser user o assistant.")
    if not isinstance(content, str) or not content or len(content) > MAX_MESSAGE_CHARS or not content.strip():
        raise ValueError("El contenido debe tener entre 1 y 4000 caracteres y no estar vacío.")
    try:
        content.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("El contenido contiene Unicode no válido.") from None
    return Message(role, content)


def _validate_context_limits(max_chars: int, max_messages: int) -> None:
    if type(max_chars) is not int or not 1 <= max_chars <= MAX_CONTEXT_CHARS:
        raise ValueError("max_chars debe ser un entero entre 1 y 20000.")
    if type(max_messages) is not int or not 1 <= max_messages <= MAX_CONTEXT_MESSAGES:
        raise ValueError("max_messages debe ser un entero entre 1 y 100.")


def _select_context(messages: list[Message], total: int, max_chars: int, max_messages: int) -> dict:
    """Selecciona un sufijo de mensajes cronológicos y cuenta todas las omisiones."""
    if messages and len(messages[-1].content) > max_chars:
        raise ValueError("El mensaje más reciente supera el presupuesto de caracteres.")
    selected: list[Message] = []
    used_chars = 0
    for message in reversed(messages):
        if len(selected) == max_messages or used_chars + len(message.content) > max_chars:
            break
        selected.append(message)
        used_chars += len(message.content)
    selected.reverse()
    return {
        "messages": [{"role": message.role, "content": message.content} for message in selected],
        "omitted_count": total - len(selected),
    }


class ConversationMemory:
    """Una sesión en proceso; no ofrece persistencia ni acceso concurrente."""

    def __init__(self):
        self._messages: list[Message] = []

    def append(self, role: str, content: str) -> None:
        message = _validate_message(role, content)
        if len(self._messages) >= MAX_HISTORY_MESSAGES:
            raise RuntimeError("La memoria de la sesión está llena.")
        self._messages.append(message)

    def context(self, max_chars: int = 4000, max_messages: int = 20) -> dict:
        _validate_context_limits(max_chars, max_messages)
        return _select_context(self._messages, len(self._messages), max_chars, max_messages)

    def clear(self) -> None:
        self._messages.clear()
