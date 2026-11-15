"""Prueba local opt-in de MCP y RAG en contenedores con red compartida."""
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

HERE = Path(__file__).resolve().parent
COMPOSE_FILE = HERE / "compose.yaml"
PROBE = (HERE / "api_probe.py").read_bytes()
QUESTION = "¿Qué color identifica los informes del proyecto Faro?"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class Fixture:
    def __init__(self, directory):
        self.project = "mcpfixture" + uuid.uuid4().hex[:12]
        self.rag_image = self.project + "-rag:local"
        self.mcp_image = self.project + "-mcp:local"
        self.read_file = directory / "read-token"
        self.write_file = directory / "write-token"
        self.read_token = secrets.token_urlsafe(32)
        self.write_token = secrets.token_urlsafe(32)
        self.redactions = [self.read_token, self.write_token]
        self.write_secret(self.read_file, self.read_token)
        self.write_secret(self.write_file, self.write_token)
        require("," not in str(directory), "La ruta temporal no puede contener comas para un montaje Docker --mount.")
        self.env = os.environ.copy()
        self.env.update({"MCP_FIXTURE_RAG_IMAGE": self.rag_image, "MCP_FIXTURE_MCP_IMAGE": self.mcp_image,
                         "MCP_FIXTURE_READ": str(self.read_file), "MCP_FIXTURE_WRITE": str(self.write_file)})
        self.compose = ["docker", "compose", "-p", self.project, "-f", str(COMPOSE_FILE)]

    @staticmethod
    def write_secret(path, value):
        # Escribir en el mismo archivo mantiene el montaje durante la prueba de rotación.
        path.write_text(value + "\n", encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o644)  # Directorio temporal 0700; los contenedores usan UID 10001.

    def run(self, command, data=None, timeout=120, check=True):
        completed = subprocess.run(command, input=data, capture_output=True, env=self.env, timeout=timeout)
        if check and completed.returncode:
            detail = completed.stderr.decode("utf-8", errors="replace")[-2000:]
            for secret in self.redactions:
                detail = detail.replace(secret, "[secret]")
            raise RuntimeError(f"Falló {' '.join(command[:3])}: {detail}")
        return completed

    def compose_run(self, *args, **kwargs):
        return self.run(self.compose + list(args), **kwargs)

    def container_id(self, service):
        result = self.compose_run("ps", "-q", service).stdout.decode().strip()
        require(len(result) >= 12 and all(c in "0123456789abcdef" for c in result), "Contenedor de prueba no disponible")
        return result

    def exec_python(self, container, script, *args):
        result = self.run(["docker", "exec", "-i", container, "python", "-", *args], data=script)
        return result.stdout.decode("utf-8").strip()

    def stats(self, model):
        script = b"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:11434/stats',timeout=3).read().decode())\n"
        return json.loads(self.exec_python(model, script))

    def last_embed_digest(self, model):
        script = b"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:11434/last_embed_digest',timeout=3).read().decode())\n"
        return json.loads(self.exec_python(model, script))["sha256"]

    def check_logs(self, api):
        collected = self.run(["docker", "logs", api])
        logs = collected.stdout + collected.stderr
        for sensitive in self.redactions + [QUESTION, "El proyecto Faro identifica sus informes"]:
            encoded_json = json.dumps(sensitive, ensure_ascii=True)[1:-1].encode("ascii")
            require(sensitive.encode("utf-8") not in logs and encoded_json not in logs,
                    "Los registros no deben revelar secretos ni contenido")

    def mount(self):
        return f"type=bind,source={self.read_file},target=/run/secrets/read,readonly"

    def mcp_args(self, network):
        return ["run", "--rm", "-i", "--label", "ai-engineering-lab.fixture=" + self.project,
                "--network", network, "--mount", self.mount(),
                "--env", "MCP_RAG_BASE_URL=http://127.0.0.1:8080",
                "--env", "MCP_RAG_READ_TOKEN_FILE=/run/secrets/read", self.mcp_image]

    def client(self, network):
        return Client(StdioServerParameters(command="docker", args=self.mcp_args(network), env=self.env),
                      mode="legacy", read_timeout_seconds=15)

    def check_mounts_and_isolation(self, api, model):
        # La API y MCP leen los archivos montados como usuarios sin privilegios;
        # los montajes no admiten escritura, y MCP no recibe el secreto de escritura.
        script = b"""import json, os
from pathlib import Path
p=Path('/run/secrets/read')
readable=bool(p.read_text().strip())
write=Path('/run/secrets/write')
def writable(path):
    try:
        path.open('a').close()
        return True
    except OSError:
        return False
print(json.dumps({'uid': os.getuid(), 'readable': readable, 'writable': writable(p),
                  'has_write_file': write.exists(),
                  'write_readable': bool(write.read_text().strip()) if write.exists() else False,
                  'write_writable': writable(write) if write.exists() else False,
                  'has_write_env': 'RAG_WRITE_TOKEN' in os.environ}))
"""
        api_state = json.loads(self.exec_python(api, script))
        require(api_state == {"uid": 10001, "readable": True, "writable": False,
                              "has_write_file": True, "write_readable": True, "write_writable": False,
                              "has_write_env": False}, "Permisos inesperados en la API")
        cmd = ["docker", "run", "--rm", "-i", "--network", "container:" + api,
               "--mount", self.mount(), "--entrypoint", "python", self.mcp_image, "-"]
        mcp_state = json.loads(self.run(cmd, data=script).stdout)
        require(mcp_state == {"uid": 10001, "readable": True, "writable": False,
                              "has_write_file": False, "write_readable": False, "write_writable": False,
                              "has_write_env": False}, "Permisos inesperados en MCP")
        inspection = json.loads(self.run(["docker", "inspect", api]).stdout)[0]
        require(not inspection["HostConfig"].get("PortBindings"), "La API no debe publicar puertos")
        for secret in self.redactions:
            require(secret not in json.dumps(inspection["Config"].get("Env", [])), "Una credencial aparece en la configuración")
        # Resolver el nombre demuestra que la red es compartida; la conexión a
        # la IP de API debe ser rechazada porque el proceso enlaza solo loopback.
        check = b"""import errno, socket
address = socket.getaddrinfo('api', 8080, type=socket.SOCK_STREAM)[0][4]
try:
    socket.create_connection(address, timeout=3)
except OSError as exc:
    if exc.errno != errno.ECONNREFUSED:
        raise
    print('refused')
else:
    raise SystemExit('La API escucha fuera de loopback')
"""
        require(self.exec_python(model, check) == "refused", "La API debe escuchar solo en loopback")

    async def first_session(self, api, model):
        async with self.client("container:" + api) as client:
            with anyio.fail_after(90):
                empty = await client.call_tool("ask_rag", {"question": QUESTION, "limit": 1})
                require(not empty.is_error, "Contrato RAG inválido al consultar el corpus vacío")
                empty_value = json.loads(empty.content[0].text)
                require(empty_value["status"] == "no_context" and empty_value["sources"] == [], "El corpus vacío debe devolver no_context")
                require(self.stats(model) == {"embed": 1, "generate": 0}, "El corpus vacío no debe generar texto")
                for question in ("😀" * 4000, "\x00" * 4000):
                    unicode_result = await client.call_tool("ask_rag", {"question": question, "limit": 1})
                    require(not unicode_result.is_error and json.loads(unicode_result.content[0].text)["status"] == "no_context",
                            "Una pregunta Unicode válida debe atravesar la API RAG")
                    require(self.last_embed_digest(model) == hashlib.sha256(question.encode("utf-8")).hexdigest(),
                            "La pregunta debe llegar íntegra al proveedor de embeddings")
                require(self.stats(model) == {"embed": 3, "generate": 0}, "Las consultas Unicode no deben generar texto")
                denied = json.loads(self.exec_python(api, PROBE, "write_with_read"))
                require(denied["status"] == 403, "El token de lectura no debe permitir escritura")
                indexed = json.loads(self.exec_python(api, PROBE, "write"))
                require(indexed["status"] == 200 and indexed["payload"]["document_id"] == "fixture_faro", "Falló la indexación")
                answer = await client.call_tool("ask_rag", {"question": QUESTION, "limit": 1})
                require(not answer.is_error, "Contrato RAG inválido al consultar con contexto")
                value = json.loads(answer.content[0].text)
                require(value["status"] == "answered", "Falló la consulta con contexto")
                require(value["answer"] == "El color es violeta [1]." and len(value["sources"]) == 1 and
                        value["sources"][0]["document_id"] == "fixture_faro" and
                        "violeta" in value["sources"][0]["text"], "Respuesta o fuente inesperada")
                require(self.stats(model) == {"embed": 5, "generate": 1}, "Cantidad inesperada de llamadas al modelo")
                invalid = await client.call_tool("ask_rag", {"question": "x" * 4001})
                require(invalid.is_error, "La pregunta excesiva debe rechazarse")
                wrong = secrets.token_urlsafe(32)
                self.redactions.append(wrong)
                self.write_secret(self.read_file, wrong)
                rejected = await client.call_tool("ask_rag", {"question": QUESTION})
                require(rejected.is_error and "autenticar" in rejected.content[0].text, "La credencial incorrecta debe rechazarse")
                require(all(value not in rejected.content[0].text for value in self.redactions + [QUESTION]),
                        "El error no debe revelar credenciales ni la pregunta")
                self.write_secret(self.read_file, self.read_token)
                require(self.stats(model) == {"embed": 5, "generate": 1}, "Una petición rechazada llegó al modelo")
                self.check_logs(api)
                self.compose_run("stop", "api", timeout=30)
                stopped = await client.call_tool("ask_rag", {"question": QUESTION})
                require(stopped.is_error, "La consulta debe fallar al detener la API")

    async def isolated_session(self):
        async with self.client("none") as client:
            with anyio.fail_after(30):
                result = await client.call_tool("ask_rag", {"question": QUESTION})
                require(result.is_error and "conectar" in result.content[0].text, "Una red independiente no debe llegar a la API")

    async def rotated_session(self, api, previous_token):
        async with self.client("container:" + api) as client:
            with anyio.fail_after(45):
                # La API ya arrancó con el nuevo token; MCP relee el archivo por llamada.
                self.write_secret(self.read_file, previous_token)
                old = await client.call_tool("ask_rag", {"question": QUESTION})
                require(old.is_error and "autenticar" in old.content[0].text, "El token anterior debe rechazarse")
                self.write_secret(self.read_file, self.read_token)
                result = await client.call_tool("ask_rag", {"question": QUESTION, "limit": 1})
                require(not result.is_error and json.loads(result.content[0].text)["status"] == "answered", "La rotación coordinada debe restaurar el acceso")

    def exercise(self):
        print("Construyendo imágenes y preparando servicios aislados...", flush=True)
        self.compose_run("build", "api", "mcp", timeout=600)
        self.compose_run("up", "-d", "--no-build", "--wait", "api", timeout=180)
        api, model = self.container_id("api"), self.container_id("model")
        self.check_mounts_and_isolation(api, model)
        anyio.run(self.isolated_session)
        anyio.run(self.first_session, api, model)
        previous = self.read_token
        self.read_token = secrets.token_urlsafe(32)
        self.redactions.append(self.read_token)
        self.write_secret(self.read_file, self.read_token)
        self.compose_run("up", "-d", "--no-build", "--no-deps", "--force-recreate", "--wait", "api", timeout=90)
        new_api = self.container_id("api")
        require(new_api != api, "La API debía recrearse")
        anyio.run(self.rotated_session, new_api, previous)
        require(self.stats(model) == {"embed": 6, "generate": 2}, "Cantidad inesperada de llamadas tras la rotación")
        self.check_logs(new_api)
        print("OK: no_context, indexación, respuesta y fuente; aislamiento, permisos, límites, parada y rotación.", flush=True)
        print("Modelo simulado: 6 llamadas de embeddings y 2 de generación; sin reintentos observados.", flush=True)

    def cleanup(self):
        # Solo contenedores etiquetados con el identificador aleatorio de esta prueba.
        containers = self.run(["docker", "ps", "-aq", "--filter", "label=ai-engineering-lab.fixture=" + self.project], check=False)
        ids = containers.stdout.decode().split()
        if ids:
            self.run(["docker", "rm", "-f", *ids], check=False)
        result = self.compose_run("down", "--volumes", "--remove-orphans", timeout=90, check=False)
        if result.returncode:
            print("No se pudo limpiar el proyecto temporal " + self.project, file=sys.stderr)
        self.run(["docker", "image", "rm", self.rag_image, self.mcp_image], timeout=60, check=False)
        if result.returncode:
            raise RuntimeError("Falló la limpieza de Docker")


def main():
    with tempfile.TemporaryDirectory(prefix="mcp-network-fixture-") as raw:
        directory = Path(raw).resolve()
        require(directory.parent == Path(tempfile.gettempdir()).resolve() and directory.name.startswith("mcp-network-fixture-"),
                "El directorio temporal no está en la ubicación esperada")
        fixture = Fixture(directory)
        try:
            fixture.exercise()
        finally:
            fixture.cleanup()


if __name__ == "__main__":
    main()
