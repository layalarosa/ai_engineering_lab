"""API HTTP interna y síncrona para un único corpus RAG."""
import hmac
import json
import math
import os
import re
import secrets
import socket
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore
from urllib.error import HTTPError, URLError

from . import ollama, openai_client
from .answer import MAX_QUESTION_CHARS, answer_question
from .ingestion import MAX_DOCUMENT_BYTES, ingest_text
from .observability import Trace
from .secret_config import read_secret
from .store import replace_document, search

ID_PATTERN = re.compile(r"/v1/documents/([A-Za-z0-9_-]{1,64})\Z")
MAX_JSON_BYTES = 32_000


@dataclass(frozen=True)
class Settings:
    dsn: str
    write_token: str
    read_token: str
    provider: str
    model: str
    generation_provider: str
    generation_model: str
    ollama_url: str
    min_score: float | None

    @classmethod
    def from_env(cls):
        env = os.environ
        provider = env.get("RAG_EMBED_PROVIDER", "ollama")
        gen_provider = env.get("RAG_GENERATION_PROVIDER", provider)
        if provider not in ("ollama", "openai") or gen_provider not in ("ollama", "openai"):
            raise ValueError("Proveedor inválido")
        write, read = read_secret("RAG_WRITE_TOKEN"), read_secret("RAG_READ_TOKEN")
        if any(not t.isascii() or len(t) < 32 or not re.fullmatch(r"[A-Za-z0-9_-]+", t) for t in (write, read)) or hmac.compare_digest(write, read):
            raise ValueError("Configura dos credenciales diferentes de al menos 32 caracteres ASCII URL-safe")
        dsn, model, gen_model = read_secret("DATABASE_URL"), env.get("RAG_EMBED_MODEL", ""), env.get("RAG_GENERATION_MODEL", "")
        if not all(x.strip() for x in (dsn, model, gen_model)) or (provider == "ollama" and model.startswith("openai:")):
            raise ValueError("Falta la base o un modelo válido")
        threshold = env.get("RAG_MIN_SCORE", "")
        score = float(threshold) if threshold else None
        if score is not None and (not math.isfinite(score) or not -1 <= score <= 1):
            raise ValueError("RAG_MIN_SCORE debe estar entre -1 y 1")
        url = env.get("OLLAMA_BASE_URL", "http://localhost:11434")
        if "ollama" in (provider, gen_provider):
            ollama._validate_connection(url, 120)
        if "openai" in (provider, gen_provider) and not read_secret("OPENAI_API_KEY").strip():
            raise ValueError("Falta OPENAI_API_KEY")
        return cls(dsn, write, read, provider, model, gen_provider, gen_model, url, score)

    @property
    def model_key(self):
        return f"openai:{self.model}" if self.provider == "openai" else self.model


class APIError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, settings, max_inflight=8):
        super().__init__(address, Handler)
        self.settings = settings
        self.slots = BoundedSemaphore(max_inflight)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request_id = secrets.token_hex(16)
                body = json.dumps({"error": {"code": "service_unavailable", "message": "Capacidad agotada.", "request_id": request_id}}).encode("utf-8")
                headers = f"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nCache-Control: no-store\r\nX-Request-ID: {request_id}\r\nConnection: close\r\n\r\n".encode("ascii")
                request.sendall(headers + body)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # El servidor base imprime una traza que puede contener datos de entrada.
        pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, format, *args):
        pass  # No registrar URLs ni cabeceras recibidas.

    def do_PUT(self):
        self.handle_api("write")

    def do_POST(self):
        self.handle_api("read")

    def _reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Request-ID", self.request_id)
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _authorize(self, role):
        values = self.headers.get_all("Authorization", [])
        header = values[0] if len(values) == 1 else ""
        token = header[7:] if header.startswith("Bearer ") else ""
        if not token.isascii():
            token = ""
        settings = self.server.settings
        is_write = hmac.compare_digest(token, settings.write_token)
        is_read = hmac.compare_digest(token, settings.read_token)
        if not (is_write or is_read):
            raise APIError(401, "unauthenticated", "Credencial inválida o ausente.")
        if (role == "write" and not is_write) or (role == "read" and not is_read):
            raise APIError(403, "forbidden", "Permiso insuficiente.")

    def _body(self, limit, media):
        if self.headers.get("Transfer-Encoding") is not None:
            raise APIError(400, "invalid_request", "Transferencia no admitida.")
        values = self.headers.get_all("Content-Length", [])
        if len(values) != 1 or not values[0].isascii() or not values[0].isdigit() or len(values[0]) > 12:
            raise APIError(400, "invalid_request", "Longitud inválida.")
        length = int(values[0])
        if length > limit:
            raise APIError(413, "payload_too_large", "El cuerpo supera el límite.")
        content_types = self.headers.get_all("Content-Type", [])
        if len(content_types) != 1 or self.headers.get_content_type() != media or self.headers.get_content_charset("utf-8").lower() != "utf-8":
            raise APIError(415, "unsupported_media_type", "Tipo de contenido no admitido.")
        data = self.rfile.read(length)
        if len(data) != length:
            raise APIError(400, "invalid_request", "Cuerpo incompleto.")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise APIError(400, "invalid_request", "El cuerpo debe ser UTF-8.") from exc

    def handle_api(self, role):
        import psycopg
        self.request_id = secrets.token_hex(16)
        trace = Trace(enabled=True)
        trace.run_id = self.request_id
        try:
            self._authorize(role)
            settings = self.server.settings
            embed = openai_client.embed if settings.provider == "openai" else ollama.embed
            generate = openai_client.generate if settings.generation_provider == "openai" else None
            match = ID_PATTERN.fullmatch(self.path) if role == "write" else None
            if role == "write":
                if not match:
                    raise APIError(400, "invalid_request", "Identificador inválido.")
                text = self._body(MAX_DOCUMENT_BYTES, "text/plain")
                if not text.strip():
                    raise APIError(422, "unprocessable_document", "El documento está vacío.")
                source = "api:" + match.group(1)
                try:
                    with trace.step("ingest"):
                        chunks = ingest_text(text, source, 500, 50)
                except ValueError as exc:
                    raise APIError(422, "unprocessable_document", "No se pudo fragmentar el documento.") from exc
                with trace.step("embed", provider=settings.provider):
                    vectors = embed([c.text for c in chunks], model=settings.model, base_url=settings.ollama_url)
                with trace.step("persist"):
                    with psycopg.connect(settings.dsn, autocommit=True, connect_timeout=10) as conn:
                        replace_document(conn, settings.model_key, source, chunks, vectors)
                self._reply(200, {"document_id": match.group(1), "chunks_indexed": len(chunks)})
            else:
                if self.path != "/v1/answers":
                    raise APIError(400, "invalid_request", "Ruta inválida.")
                try:
                    request = json.loads(self._body(MAX_JSON_BYTES, "application/json"))
                except (ValueError, RecursionError) as exc:
                    raise APIError(400, "invalid_request", "JSON inválido.") from exc
                if not isinstance(request, dict) or set(request) - {"question", "limit"}:
                    raise APIError(400, "invalid_request", "Campos inválidos.")
                question, limit = request.get("question"), request.get("limit", 3)
                if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_CHARS or type(limit) is not int or not 1 <= limit <= 20:
                    raise APIError(400, "invalid_request", "Pregunta o límite inválido.")
                with trace.step("embed", provider=settings.provider):
                    vector = embed([question], model=settings.model, base_url=settings.ollama_url)[0]
                with trace.step("retrieve"):
                    with psycopg.connect(settings.dsn, autocommit=True, connect_timeout=10) as conn:
                        results = search(conn, settings.model_key, vector, limit, source_prefix="api:")
                with trace.step("answer", provider=settings.generation_provider):
                    answer = answer_question(question, results, model=settings.generation_model, base_url=settings.ollama_url, min_score=settings.min_score, generate_fn=generate)
                sources = [{"label": f"[{i}]", "document_id": r.chunk.source[4:], "chunk_index": r.chunk.index,
                            "score": r.score, "text": r.chunk.text} for i, r in enumerate(answer.sources, 1)]
                self._reply(200, {"status": "answered" if sources else "no_context", "answer": answer.text, "sources": sources})
        except APIError as exc:
            self._reply(exc.status, {"error": {"code": exc.code, "message": exc.message, "request_id": self.request_id}})
        except (ollama.OllamaError, openai_client.OpenAIError) as exc:
            cause = exc.__cause__
            timed_out = isinstance(cause, (TimeoutError, socket.timeout)) or (isinstance(cause, URLError) and isinstance(cause.reason, (TimeoutError, socket.timeout)))
            status, code = (504, "upstream_timeout") if timed_out else ((503, "service_unavailable") if isinstance(cause, (URLError, OSError)) and not isinstance(cause, HTTPError) else (502, "upstream_error"))
            self._reply(status, {"error": {"code": code, "message": "Servicio de modelo no disponible.", "request_id": self.request_id}})
        except psycopg.Error:
            self._reply(503, {"error": {"code": "service_unavailable", "message": "Base de datos no disponible.", "request_id": self.request_id}})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            self.close_connection = True
        except Exception:
            self._reply(500, {"error": {"code": "internal_error", "message": "Error interno.", "request_id": self.request_id}})


def main():
    try:
        settings = Settings.from_env()
        port = int(os.environ.get("RAG_PORT", "8080"))
        if not 1 <= port <= 65535:
            raise ValueError("RAG_PORT debe estar entre 1 y 65535")
        server = Server(("127.0.0.1", port), settings)
    except (ValueError, OSError) as exc:
        print(f"Configuración inválida: {exc}", file=sys.stderr)
        return 1
    print(f"API RAG disponible en 127.0.0.1:{port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
