"""Eventos operativos opcionales sin contenido del usuario."""

import json
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone


class Trace:
    """Mide etapas con un identificador por ejecución y campos controlados."""

    def __init__(self, enabled=False, stream=None):
        self.enabled = enabled
        self.stream = stream if stream is not None else sys.stderr
        self.run_id = uuid.uuid4().hex if enabled else None

    @contextmanager
    def step(self, name, *, provider=None):
        start = time.monotonic()
        try:
            yield
        except Exception:
            self._emit(name, "error", start, provider)
            raise
        else:
            self._emit(name, "ok", start, provider)

    def _emit(self, name, status, start, provider):
        if not self.enabled:
            return
        event = {"timestamp": datetime.now(timezone.utc).isoformat(), "run_id": self.run_id,
                 "stage": name, "status": status,
                 "duration_ms": round((time.monotonic() - start) * 1000, 2)}
        if provider is not None:
            event["provider"] = provider
        print(json.dumps(event, ensure_ascii=True), file=self.stream, flush=True)
