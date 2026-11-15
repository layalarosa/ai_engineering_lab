"""Solicitudes de preparación dentro del contenedor de la API; sin imprimir tokens."""
import http.client
import json
import sys
from pathlib import Path

action = sys.argv[1]
token_file = "/run/secrets/read" if action == "write_with_read" else "/run/secrets/write"
token = Path(token_file).read_text(encoding="utf-8").strip()
connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=135)
try:
    body = "El proyecto Faro identifica sus informes con el color violeta.".encode("utf-8")
    connection.request("PUT", "/v1/documents/fixture_faro", body=body,
                       headers={"Authorization": "Bearer " + token, "Content-Type": "text/plain; charset=utf-8"})
    response = connection.getresponse()
    payload = json.loads(response.read())
    print(json.dumps({"status": response.status, "payload": payload}))
finally:
    connection.close()
