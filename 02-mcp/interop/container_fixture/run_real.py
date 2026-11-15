"""Prueba opt-in con Ollama del host usando Docker Desktop y un relay temporal."""
import json
import os
import tempfile
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

from run import COMPOSE_FILE, HERE, PROBE, QUESTION, Fixture, require


class RealFixture(Fixture):
    def __init__(self, directory):
        super().__init__(directory)
        self.embed_model = os.environ.get("RAG_LIVE_EMBED_MODEL", "nomic-embed-text")
        self.generation_model = os.environ.get("RAG_LIVE_GENERATION_MODEL", "llama3.1:8b")
        require(self.embed_model.strip() and self.generation_model.strip(), "Configura modelos de Ollama válidos")
        self.env.update({"MCP_FIXTURE_EMBED_MODEL": self.embed_model,
                         "MCP_FIXTURE_GENERATION_MODEL": self.generation_model})
        self.compose = ["docker", "compose", "-p", self.project, "-f", str(COMPOSE_FILE),
                        "-f", str(HERE / "compose.real.yaml")]

    def check_networks(self, api, model):
        api_info = json.loads(self.run(["docker", "inspect", api]).stdout)[0]
        relay_info = json.loads(self.run(["docker", "inspect", model]).stdout)[0]
        api_networks = set(api_info["NetworkSettings"]["Networks"])
        relay_networks = set(relay_info["NetworkSettings"]["Networks"])
        require(len(api_networks) == 1 and len(relay_networks) == 2 and api_networks < relay_networks,
                "Solo el intermediario debe estar conectado a la red adicional")
        require(not api_info["HostConfig"].get("PortBindings") and not relay_info["HostConfig"].get("PortBindings"),
                "La API y el intermediario no deben publicar puertos")
        for secret in self.redactions:
            require(secret not in json.dumps(api_info["Config"].get("Env", [])), "Una credencial aparece en la configuración")
        # Comprobar conectividad y restricciones desde la propia red de la API.
        probe = """import http.client, json, socket, sys
try:
    s = socket.create_connection(('host.docker.internal', 11434), timeout=3)
except OSError:
    pass
else:
    s.close()
    raise SystemExit('La API tiene acceso directo no esperado al host')
def request(method, path):
    c=http.client.HTTPConnection('model', 11434, timeout=10)
    try:
        c.request(method, path)
        r=c.getresponse()
        return r.status, r.read()
    finally:
        c.close()
status, payload = request('GET', '/api/tags')
if status != 200:
    raise SystemExit('Ollama no responde a través del intermediario')
names={item.get('name') for item in json.loads(payload)['models']}
if not all(name in names or name + ':latest' in names for name in sys.argv[1:]):
    raise SystemExit('Faltan modelos de Ollama requeridos')
if request('GET', '/api/version')[0] != 404:
    raise SystemExit('El intermediario aceptó una ruta no permitida')
if request('GET', '/api/generate')[0] != 405:
    raise SystemExit('El intermediario aceptó un método no permitido')
if request('GET', '/api/tags?extra=1')[0] != 400:
    raise SystemExit('El intermediario aceptó una consulta no permitida')
print('ok')
""".encode("utf-8")
        require(self.exec_python(api, probe, self.embed_model, self.generation_model) == "ok", "Falló la comprobación de red")

    async def exercise_mcp(self, api):
        params = StdioServerParameters(command="docker", args=self.mcp_args("container:" + api), env=self.env)
        async with Client(params, mode="legacy", read_timeout_seconds=290) as client:
            with anyio.fail_after(330):
                empty = await client.call_tool("ask_rag", {"question": QUESTION, "limit": 1})
                require(not empty.is_error, "Falló la consulta al corpus vacío")
                empty_value = json.loads(empty.content[0].text)
                require(empty_value["status"] == "no_context" and not empty_value["sources"], "La base vacía debe devolver no_context")
                denied = json.loads(self.exec_python(api, PROBE, "write_with_read"))
                require(denied["status"] == 403, "El token de lectura no debe permitir escritura")
                indexed = json.loads(self.exec_python(api, PROBE, "write"))
                require(indexed["status"] == 200 and indexed["payload"]["document_id"] == "fixture_faro", "Falló la indexación")
                answer = await client.call_tool("ask_rag", {"question": QUESTION, "limit": 1})
                require(not answer.is_error, "Falló la consulta con Ollama real")
                value = json.loads(answer.content[0].text)
                require(value["status"] == "answered" and bool(value["answer"].strip()), "No se recibió una respuesta")
                require(len(value["sources"]) == 1 and value["sources"][0]["document_id"] == "fixture_faro" and
                        "violeta" in value["sources"][0]["text"], "La fuente recuperada no corresponde al documento")
                print("Respuesta del modelo (hasta 1000 caracteres):", value["answer"][:1000], flush=True)
                print("Fuente: documento temporal, fragmento", value["sources"][0]["chunk_index"], flush=True)

    def exercise(self):
        print("Preparando API y base aisladas con acceso acotado a Ollama del host...", flush=True)
        self.compose_run("build", "api", "mcp", timeout=600)
        self.compose_run("up", "-d", "--no-build", "--wait", "api", timeout=180)
        api, model = self.container_id("api"), self.container_id("model")
        self.check_networks(api, model)
        anyio.run(self.exercise_mcp, api)
        self.check_logs(api)
        self.check_logs(model)
        print("OK: MCP, RAG y pgvector en contenedores; embeddings y generación con Ollama real.", flush=True)


def main():
    with tempfile.TemporaryDirectory(prefix="mcp-network-fixture-") as raw:
        directory = Path(raw).resolve()
        require(directory.parent == Path(tempfile.gettempdir()).resolve() and directory.name.startswith("mcp-network-fixture-"),
                "El directorio temporal no está en la ubicación esperada")
        fixture = RealFixture(directory)
        try:
            fixture.exercise()
        finally:
            fixture.cleanup()


if __name__ == "__main__":
    main()
