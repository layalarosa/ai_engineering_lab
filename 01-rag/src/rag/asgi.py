"""Adaptación ASGI acotada de la API síncrona existente."""
import asyncio
import io
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from threading import BoundedSemaphore
from types import SimpleNamespace

from .api import APIError, Handler, MAX_JSON_BYTES, Settings
from .ingestion import MAX_DOCUMENT_BYTES


class RequestAdapter(Handler):
    """Reutiliza validación, autorización y operaciones de la API local."""

    def __init__(self, path, headers, body, settings):
        self.path = path
        self.headers = headers
        self.rfile = io.BytesIO(body)
        self.server = SimpleNamespace(settings=settings)
        self.request_id = secrets.token_hex(16)
        self.result = None

    def _reply(self, status, payload):
        self.result = status, payload, self.request_id


class Application:
    def __init__(self, settings, capacity=4):
        self.settings = settings
        self.slots = BoundedSemaphore(capacity)
        self.executor = ThreadPoolExecutor(max_workers=capacity, thread_name_prefix="rag-asgi")

    @staticmethod
    async def reply(send, status, payload, request_id):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers = [(b"content-type", b"application/json; charset=utf-8"), (b"cache-control", b"no-store"),
                   (b"x-request-id", request_id.encode("ascii")), (b"content-length", str(len(body)).encode("ascii"))]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif event["type"] == "lifespan.shutdown":
                    self.executor.shutdown(wait=False)
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        request_id = secrets.token_hex(16)
        method = scope.get("method")
        if method not in ("PUT", "POST"):
            await self.reply(send, 405, {"error": {"code": "method_not_allowed", "message": "Método no admitido.", "request_id": request_id}}, request_id)
            return
        headers = Message()
        for name, value in scope.get("headers", []):
            headers[name.decode("latin-1")] = value.decode("latin-1")
        # El adaptador local rechaza rutas con parámetros y conserva cabeceras duplicadas.
        path = scope.get("path", "") + ("?" if scope.get("query_string") else "")
        adapter = RequestAdapter(path, headers, b"", self.settings)
        role = "write" if method == "PUT" else "read"
        try:
            adapter._authorize(role)
        except APIError as exc:
            await self.reply(send, exc.status, {"error": {"code": exc.code, "message": exc.message, "request_id": request_id}}, request_id)
            return
        max_bytes = MAX_DOCUMENT_BYTES if role == "write" else MAX_JSON_BYTES
        body = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            if event["type"] != "http.request":
                continue
            body.extend(event.get("body", b""))
            if len(body) > max_bytes:
                await self.reply(send, 413, {"error": {"code": "payload_too_large", "message": "El cuerpo supera el límite.", "request_id": request_id}}, request_id)
                return
            if not event.get("more_body", False):
                break
        adapter.rfile = io.BytesIO(body)
        if not self.slots.acquire(blocking=False):
            await self.reply(send, 503, {"error": {"code": "service_unavailable", "message": "Capacidad agotada.", "request_id": request_id}}, request_id)
            return
        try:
            future = self.executor.submit(adapter.handle_api, role)
        except BaseException:
            self.slots.release()
            raise
        # La ranura se libera al terminar el hilo, incluso si el cliente se desconecta.
        future.add_done_callback(lambda _: self.slots.release())
        await asyncio.wrap_future(future)
        status, payload, request_id = adapter.result
        await self.reply(send, status, payload, request_id)


def create_app():
    return Application(Settings.from_env())
