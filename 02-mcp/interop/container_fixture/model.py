"""Modelo falso y contadores para la prueba de red, sin llamadas externas."""
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

counts = {"embed": 0, "generate": 0}
last_embed_digest = None
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def reply(self, value):
        body = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.reply({"ok": True})
        elif self.path == "/stats":
            with lock:
                self.reply(dict(counts))
        elif self.path == "/last_embed_digest":
            with lock:
                self.reply({"sha256": last_embed_digest})
        else:
            self.send_error(404)

    def do_POST(self):
        global last_embed_digest
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/embed":
            with lock:
                counts["embed"] += 1
                last_embed_digest = hashlib.sha256(data["input"][0].encode("utf-8")).hexdigest()
            self.reply({"embeddings": [[1.0, 0.0] for _ in data["input"]]})
        elif self.path == "/api/generate":
            with lock:
                counts["generate"] += 1
            self.reply({"response": "El color es violeta [1].", "done": True})
        else:
            self.send_error(404)

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 11434), Handler).serve_forever()
