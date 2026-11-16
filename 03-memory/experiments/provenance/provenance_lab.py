"""Experimento en proceso; la identidad y la autorización son simuladas."""
import json
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from memory_lab.memory import ConversationMemory, MAX_HISTORY_MESSAGES

ALLOWED_KEYS = frozenset({"perfil/idioma_preferido", "perfil/editor_preferido"})


class ConflictError(RuntimeError):
    """La solicitud ya no coincide con el estado actual."""


@dataclass(frozen=True, slots=True)
class Statement:
    statement_id: UUID
    message_id: UUID
    key: str
    value: str
    content: str
    actor: str
    supersedes_statement_id: UUID | None
    accepted_at: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class Operation:
    actor: str
    operation_id: UUID
    request: tuple
    statement: Statement


@dataclass(frozen=True, slots=True)
class Session:
    generation: UUID
    statements: tuple[Statement, ...] = ()
    operations: tuple[Operation, ...] = ()


def _uuid(value):
    if not isinstance(value, UUID):
        raise ValueError("Identificador inválido.")
    return value


def _key(value):
    if not isinstance(value, str) or value not in ALLOWED_KEYS:
        raise ValueError("Clave no admitida.")
    return value


def preview(key, value):
    """Prepara exactamente el mensaje que se escribiría tras confirmarlo."""
    _key(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError("Valor inválido.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Valor inválido.") from None
    content = json.dumps({"clave": key, "valor": value}, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    # Usa el contrato público de Memory para validar el mensaje canónico.
    try:
        validator = ConversationMemory()
        validator.append("user", content)
    except ValueError:
        raise ValueError("El mensaje canónico no es válido.") from None
    return content


def _public(statement):
    return {"statement_id": statement.statement_id, "message_id": statement.message_id,
            "key": statement.key, "value": statement.value,
            "message": {"role": "user", "content": statement.content},
            "simulated_actor": statement.actor, "supersedes_statement_id": statement.supersedes_statement_id,
            "accepted_at": statement.accepted_at, "simulated_policy_version": statement.policy_version}


class ProvenanceLab:
    """Una instancia local con una política fija y simulada, común a sus sesiones."""

    def __init__(self, simulated_allowed_actors):
        if (not isinstance(simulated_allowed_actors, (set, frozenset))
                or not simulated_allowed_actors
                or any(not isinstance(actor, str) or not actor.strip() for actor in simulated_allowed_actors)):
            raise ValueError("Política simulada inválida.")
        self._allowed = frozenset(simulated_allowed_actors)
        self._sessions = {}
        self._lock = threading.RLock()

    def _authorize(self, simulated_actor):
        if not isinstance(simulated_actor, str) or simulated_actor not in self._allowed:
            raise PermissionError("Acceso denegado por la política simulada.")

    def _session(self, session_id, generation):
        _uuid(session_id)
        _uuid(generation)
        session = self._sessions.get(session_id)
        if session is None:
            raise LookupError("Sesión inexistente.")
        if session.generation != generation:
            raise ConflictError("La generación de la sesión cambió.")
        return session

    def create_session(self, simulated_actor, session_id):
        self._authorize(simulated_actor)
        _uuid(session_id)
        with self._lock:
            if session_id in self._sessions:
                raise ConflictError("La sesión ya existe.")
            session = Session(generation=uuid4())
            self._sessions[session_id] = session
            return session.generation

    def confirm(self, simulated_actor, session_id, command):
        """Confirma una afirmación; la confirmación es una llamada simulada, sin UI."""
        self._authorize(simulated_actor)
        if not isinstance(command, dict):
            raise ValueError("Solicitud inválida.")
        command = dict(command)
        required = {"generation", "operation_id", "key", "value"}
        if set(command) not in (required, required | {"supersedes_statement_id"}):
            raise ValueError("Solicitud inválida.")
        generation = _uuid(command["generation"])
        operation_id = _uuid(command["operation_id"])
        previous = _uuid(command["supersedes_statement_id"]) if "supersedes_statement_id" in command else None
        key, value = _key(command["key"]), command["value"]
        content = preview(key, value)
        request = (key, value, previous)
        with self._lock:
            session = self._session(session_id, generation)
            # La deduplicación precede a la comprobación de la versión vigente.
            for operation in session.operations:
                if operation.actor == simulated_actor and operation.operation_id == operation_id:
                    if operation.request != request:
                        raise ConflictError("El identificador de operación ya se usó con otra solicitud.")
                    return _public(operation.statement)
            current = next((entry for entry in reversed(session.statements) if entry.key == key), None)
            if (current is None and previous is not None) or (current is not None and
                    (previous is None or previous != current.statement_id)):
                raise ConflictError("La versión anterior no coincide con la vigente.")
            if len(session.statements) >= MAX_HISTORY_MESSAGES:
                raise RuntimeError("La memoria de la sesión está llena.")
            entry = Statement(statement_id=uuid4(), message_id=uuid4(), key=key, value=value,
                              content=content, actor=simulated_actor, supersedes_statement_id=previous,
                              accepted_at=datetime.now(timezone.utc).isoformat(), policy_version="simulada-v1")
            operation = Operation(actor=simulated_actor, operation_id=operation_id, request=request, statement=entry)
            # Se construye el nuevo estado completo antes de publicar la referencia.
            updated = Session(generation=session.generation, statements=session.statements + (entry,),
                              operations=session.operations + (operation,))
            self._sessions[session_id] = updated
            return _public(entry)

    def current(self, simulated_actor, session_id, generation, key):
        self._authorize(simulated_actor)
        _key(key)
        with self._lock:
            session = self._session(session_id, generation)
            entry = next((item for item in reversed(session.statements) if item.key == key), None)
            return _public(entry) if entry is not None else None

    def history(self, simulated_actor, session_id, generation):
        self._authorize(simulated_actor)
        with self._lock:
            session = self._session(session_id, generation)
            return [_public(item) for item in session.statements]

    def clear(self, simulated_actor, session_id, generation):
        self._authorize(simulated_actor)
        with self._lock:
            self._session(session_id, generation)
            new_generation = uuid4()
            self._sessions[session_id] = Session(generation=new_generation)
            return new_generation

    def delete(self, simulated_actor, session_id, generation):
        self._authorize(simulated_actor)
        with self._lock:
            self._session(session_id, generation)
            del self._sessions[session_id]
