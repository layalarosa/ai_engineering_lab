"""Cliente de carga sintética; solo se conecta al proxy del Compose de prueba."""
import argparse
import http.client
import json
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor


class FixtureConnection(http.client.HTTPSConnection):
    def connect(self):
        raw = socket.create_connection(("proxy", 8443), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname="localhost")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-at", type=float, required=True)
    parser.add_argument("--duration", type=int, default=12)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.duration != 12 or args.workers != 2:
        parser.error("esta prueba fija duración y concurrencia")
    context = ssl.create_default_context(cafile="/certs/cert.pem")
    deadline = args.start_at + args.duration
    wait = max(0, args.start_at - time.time())
    time.sleep(wait)
    start = time.time()
    if start >= deadline - 6:
        raise RuntimeError("cliente inició demasiado tarde para la medición")
    barrier = threading.Barrier(args.workers)

    def worker():
        results = []
        barrier.wait(timeout=5)
        while time.time() < deadline:
            conn = FixtureConnection("proxy", 8443, context=context, timeout=10)
            begin = time.monotonic()
            try:
                conn.request("POST", "/v1/answers", b'{"question":"vector"}',
                             {"Authorization": "Bearer test_read_token_only_0000000000000000000", "Content-Type": "application/json"})
                response = conn.getresponse()
                body = response.read()
                status = response.status
                if status == 200:
                    try:
                        data = json.loads(body)
                        if data.get("status") != "answered" or not any(s.get("document_id") == "prueba" for s in data.get("sources", [])):
                            status = "invalid_response"
                    except (ValueError, AttributeError, TypeError):
                        status = "invalid_response"
            except (OSError, http.client.HTTPException):
                status = "connection_error"
            finally:
                conn.close()
            results.append({"status": status, "elapsed_ms": round((time.monotonic() - begin) * 1000, 2)})
            time.sleep(.2)
        return results

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        records = [record for group in pool.map(lambda _: worker(), range(args.workers)) for record in group]
    print(json.dumps({"client_ip": socket.gethostbyname(socket.gethostname()), "records": records}))


if __name__ == "__main__":
    main()
