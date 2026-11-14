"""Proveedor falso exclusivo de la prueba aislada del proxy."""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        time.sleep(float(os.getenv("FIXTURE_DELAY_SECONDS", "0")))
        if self.path == "/api/embed":
            response = {"embeddings": [[1.0, 0.0] for _ in data["input"]]}
        elif self.path == "/api/generate":
            response = {"response": "Respuesta de prueba [1].", "done": True}
        else:
            self.send_error(404)
            return
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", 11434), Handler).serve_forever()
