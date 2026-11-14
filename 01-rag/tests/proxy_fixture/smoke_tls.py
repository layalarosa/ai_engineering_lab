"""Comprobación del proxy de laboratorio con credenciales y datos de prueba."""
import argparse
import http.client
import json
import ssl
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ca", required=True)
    parser.add_argument("--port", type=int, default=18443)
    args = parser.parse_args()
    context = ssl.create_default_context(cafile=args.ca)
    deadline = time.monotonic() + 20
    while True:
        probe = http.client.HTTPSConnection("localhost", args.port, context=context, timeout=3)
        try:
            probe.connect()
            break
        except (ConnectionRefusedError, ConnectionResetError, TimeoutError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(.2)
        finally:
            probe.close()
    untrusted = http.client.HTTPSConnection("localhost", args.port, context=ssl.create_default_context(), timeout=5)
    try:
        try:
            untrusted.connect()
        except ssl.SSLCertVerificationError:
            pass
        else:
            raise AssertionError("se aceptó un certificado no confiable")
    finally:
        untrusted.close()
    bad_host = http.client.HTTPSConnection("127.0.0.1", args.port, context=context, timeout=5)
    try:
        try:
            bad_host.connect()
        except ssl.SSLCertVerificationError:
            pass
        else:
            raise AssertionError("el certificado no rechazó un nombre incorrecto")
    finally:
        bad_host.close()

    def request(method, path, token, content_type, body):
        conn = http.client.HTTPSConnection("localhost", args.port, context=context, timeout=30)
        try:
            conn.request(method, path, body, {"Authorization": "Bearer " + token, "Content-Type": content_type})
            response = conn.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), json.loads(data)
        finally:
            conn.close()

    write = "test_write_token_only_000000000000000000"
    read = "test_read_token_only_0000000000000000000"
    status, _, body = request("POST", "/v1/answers", write, "application/json", b'{"question":"q"}')
    assert status == 403 and body["error"]["code"] == "forbidden", (status, body)
    status, _, body = request("PUT", "/v1/documents/prueba", write, "text/plain; charset=utf-8", b"Un vector representa texto.")
    assert status == 200 and body["chunks_indexed"] == 1, (status, body)
    status, _, body = request("POST", "/v1/answers", read, "application/json", b'{"question":"vector"}')
    assert status == 200 and body["status"] == "answered" and body["sources"][0]["document_id"] == "prueba", (status, body)
    status, headers, body = request("POST", "/v1/answers", read, "application/json", b'{"question":"vector"}')
    assert status == 429 and headers.get("Retry-After") == "10", (status, headers, body)
    print("TLS, autorización, indexación, consulta y límite de tasa verificados.")


if __name__ == "__main__":
    main()
