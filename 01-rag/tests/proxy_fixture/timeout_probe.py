"""Verifica el timeout del proxy con un proveedor lento simulado."""
import argparse
import http.client
import json
import ssl
import time

WRITE = "test_write_token_only_000000000000000000"
READ = "test_read_token_only_0000000000000000000"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ca", required=True)
    parser.add_argument("--port", type=int, default=18443)
    args = parser.parse_args()
    context = ssl.create_default_context(cafile=args.ca)

    def request(method, path, token, body, media):
        start = time.monotonic()
        conn = http.client.HTTPSConnection("localhost", args.port, context=context, timeout=15)
        try:
            conn.request(method, path, body, {"Authorization": "Bearer " + token, "Content-Type": media})
            response = conn.getresponse()
            response.read()
            return response.status, round(time.monotonic() - start, 3)
        finally:
            conn.close()

    deadline = time.monotonic() + 20
    while True:
        try:
            status, _ = request("PUT", "/v1/documents/timeout", WRITE, b"texto de prueba", "text/plain")
            if status == 200:
                break
        except (ConnectionError, OSError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("el servicio no inició")
        time.sleep(.25)
    status, elapsed = request("POST", "/v1/answers", READ, b'{"question":"texto"}', "application/json")
    if status != 504:
        raise AssertionError(f"se esperaba 504 del proxy, se recibió {status}")
    # El proxy puede cortar la respuesta sin cancelar el trabajo síncrono.
    time.sleep(3)
    recovered, _ = request("PUT", "/v1/documents/timeout", WRITE, b"texto de prueba", "text/plain")
    if recovered != 200:
        raise AssertionError(f"el servicio no aceptó otra operación: {recovered}")
    print(json.dumps({"proxy_status": status, "elapsed_seconds": elapsed, "subsequent_write": recovered,
                      "note": "El 504 del proxy no demuestra cancelación del trabajo de modelo."}, indent=2))


if __name__ == "__main__":
    main()
