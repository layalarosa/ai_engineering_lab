"""Comprueba la imagen con datos sintéticos y sin red."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command(args, *, env=None):
    return subprocess.run(args, cwd=ROOT, env=env, capture_output=True, timeout=60)


def check(image):
    with tempfile.TemporaryDirectory(prefix="evals-docker-test-") as raw:
        folder = Path(raw).resolve()
        if folder.parent != Path(tempfile.gettempdir()).resolve() or not folder.name.startswith("evals-docker-test-"):
            raise RuntimeError("La ruta temporal de pruebas no es válida.")
        if "," in str(folder):
            raise RuntimeError("La ruta temporal no puede contener comas para el montaje Docker.")
        # Solo se comparte contenido sintético; el contenedor usa un UID distinto.
        os.chmod(folder, 0o755)
        cases = folder / "cases.jsonl"
        cases.write_bytes(b'{"id":"a","input":"PRIVATE_INPUT","expected_output":"yes"}\n'
                          b'{"id":"b","input":"","expected_output":""}\n')
        outputs = {
            "passed.jsonl": b'{"id":"b","output":""}\n{"id":"a","output":"yes"}\n',
            "failed.jsonl": b'{"id":"a","output":"no"}\n',
            "invalid.jsonl": b'{"id":"a","output":',
        }
        for name, data in outputs.items():
            (folder / name).write_bytes(data)
        (folder / "example-cases.jsonl").write_bytes((ROOT / "examples" / "cases.jsonl").read_bytes())
        (folder / "example-outputs.jsonl").write_bytes((ROOT / "examples" / "outputs.jsonl").read_bytes())
        for name in ("retrieval_corpus.jsonl", "retrieval_cases.jsonl", "retrieval_outputs.jsonl"):
            (folder / name).write_bytes((ROOT / "examples" / name).read_bytes())
        for name in ("rag_report.json", "rag_corpus.jsonl", "rag_cases.jsonl"):
            (folder / name).write_bytes((ROOT / "examples" / name).read_bytes())
        for name in ("grounding_cases.jsonl", "grounding_outputs.jsonl", "grounding_reviews.jsonl"):
            (folder / name).write_bytes((ROOT / "examples" / name).read_bytes())
        for name in ("completeness_cases.jsonl", "completeness_outputs.jsonl", "completeness_reviews.jsonl"):
            (folder / name).write_bytes((ROOT / "examples" / name).read_bytes())
        for name in ("live_metrics_cases.jsonl", "live_metrics_context.json"):
            (folder / name).write_bytes((ROOT / "examples" / name).read_bytes())
        (folder / "completeness_empty_reviews.jsonl").write_bytes(b"")
        (folder / "completeness_invalid_reviews.jsonl").write_bytes(
            (folder / "completeness_reviews.jsonl").read_bytes().replace(
                b"5e7cd9e3b47f75be1c567a60dbdd8066809844672c49377e5f9c8f3c74062bd3", b"0" * 64))
        (folder / "rag_grounding_cases.jsonl").write_bytes((ROOT / "examples" / "rag_grounding_cases.jsonl").read_bytes())
        (folder / "rag_grounding_bad_cases.jsonl").write_bytes(
            (folder / "rag_grounding_cases.jsonl").read_bytes().replace(b"Pregunta positiva", b"Pregunta diferente"))
        (folder / "grounding_empty_reviews.jsonl").write_bytes(b"")
        (folder / "grounding_invalid_reviews.jsonl").write_bytes(
            (folder / "grounding_reviews.jsonl").read_bytes().replace(
                b"e50ef270fe741d43c9549eb2b01d51675022df45ac1b2f4c3193c14489e40ac2", b"0" * 64))
        legacy_review = json.loads((folder / "grounding_reviews.jsonl").read_bytes())
        del legacy_review["schema_version"], legacy_review["case_sha256"]
        (folder / "grounding_legacy_reviews.jsonl").write_bytes(
            (json.dumps(legacy_review, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii"))
        (folder / "retrieval_pass_cases.jsonl").write_bytes(
            b'{"id":"ok","input":"","relevant_document_ids":["DOC_A"]}\n')
        (folder / "retrieval_pass_outputs.jsonl").write_bytes(
            b'{"id":"ok","retrieved_document_ids":["DOC_A"]}\n')
        for path in folder.iterdir():
            os.chmod(path, 0o644)
        mount = f"type=bind,source={folder},target=/data,readonly"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        env["PYTHONUTF8"] = "1"
        scenarios = (("cases.jsonl", "passed.jsonl", 0, 2), ("cases.jsonl", "failed.jsonl", 1, 2),
                     ("cases.jsonl", "invalid.jsonl", 2, None),
                     ("example-cases.jsonl", "example-outputs.jsonl", 1, 4))
        for cases_name, filename, expected_code, expected_total in scenarios:
            local = command([sys.executable, "-S", "-m", "evals_lab", "--cases", str(folder / cases_name),
                             "--outputs", str(folder / filename)], env=env)
            container = command(["docker", "run", "--rm", "--network", "none", "--read-only",
                                 "--mount", mount, image, "--cases", "/data/" + cases_name,
                                 "--outputs", "/data/" + filename])
            if local.returncode != expected_code or container.returncode != expected_code:
                raise RuntimeError(f"Código inesperado en {filename}: local={local.returncode}, Docker={container.returncode}; "
                                   f"Docker stderr={container.stderr.decode('utf-8', errors='replace')[:500]}")
            if local.stdout != container.stdout or local.stderr.replace(b"\r\n", b"\n") != container.stderr.replace(b"\r\n", b"\n"):
                raise RuntimeError(f"La imagen y la ejecución local difieren en {filename}.")
            if b"PRIVATE_INPUT" in container.stdout + container.stderr or str(folder).encode() in container.stderr:
                raise RuntimeError("La imagen expuso texto o rutas de entrada.")
            if expected_code != 2:
                report = json.loads(container.stdout)
                if report["total"] != expected_total:
                    raise RuntimeError("El informe de la imagen no contiene los casos esperados.")
        retrieval_args = ["--corpus", "retrieval_corpus.jsonl", "--cases", "retrieval_cases.jsonl",
                          "--outputs", "retrieval_outputs.jsonl"]
        def paths(base):
            return [base + "/" + value if value.endswith(".jsonl") else value for value in retrieval_args]
        for pass_case, extra, expected_code in ((False, [], 1), (False, ["--k", "0"], 2), (True, [], 0)):
            args = paths(folder.as_posix())
            docker_args = paths('/data')
            if pass_case:
                args[3], args[5] = folder.as_posix() + '/retrieval_pass_cases.jsonl', folder.as_posix() + '/retrieval_pass_outputs.jsonl'
                docker_args[3], docker_args[5] = '/data/retrieval_pass_cases.jsonl', '/data/retrieval_pass_outputs.jsonl'
            local = command([sys.executable, "-S", "-m", "evals_lab.retrieval", *args, *extra], env=env)
            container = command(["docker", "run", "--rm", "--network", "none", "--read-only", "--mount", mount,
                                 "--entrypoint", "python", image, "-S", "-m", "evals_lab.retrieval",
                                 *docker_args, *extra])
            if (local.returncode != expected_code or container.returncode != expected_code or
                    local.stdout != container.stdout or
                    local.stderr.replace(b"\r\n", b"\n") != container.stderr.replace(b"\r\n", b"\n")):
                raise RuntimeError("La evaluación de recuperación difiere entre ejecución local y Docker.")
        for k, expected_code in (("2", 0), ("5", 2)):
            arguments = ["--report", "rag_report.json", "--corpus", "rag_corpus.jsonl",
                         "--cases", "rag_cases.jsonl", "--k", k]
            def adapter_paths(base):
                return [base + "/" + value if value.endswith((".json", ".jsonl")) else value
                        for value in arguments]
            local = command([sys.executable, "-S", "-m", "evals_lab.rag_adapter",
                             *adapter_paths(folder.as_posix())], env=env)
            container = command(["docker", "run", "--rm", "--network", "none", "--read-only", "--mount", mount,
                                 "--entrypoint", "python", image, "-S", "-m", "evals_lab.rag_adapter",
                                 *adapter_paths('/data')])
            if (local.returncode != expected_code or container.returncode != expected_code or
                    local.stdout != container.stdout or
                    local.stderr.replace(b"\r\n", b"\n") != container.stderr.replace(b"\r\n", b"\n")):
                raise RuntimeError("La conversión RAG difiere entre ejecución local y Docker.")
            if expected_code == 0 and (b'"retrieved_document_ids":["A","B","C"]' not in container.stdout or
                                       b'Fragmento' in container.stdout):
                raise RuntimeError("La imagen no convirtió el informe RAG como se esperaba.")
        for review_name, extra, expected_code in (("grounding_reviews.jsonl", [], 0),
                                                  ("grounding_empty_reviews.jsonl", [], 1),
                                                  ("grounding_invalid_reviews.jsonl", [], 2),
                                                  ("grounding_legacy_reviews.jsonl", [], 2),
                                                  ("grounding_legacy_reviews.jsonl", ["--allow-unbound-reviews"], 1)):
            arguments = ["--cases", "grounding_cases.jsonl", "--outputs", "grounding_outputs.jsonl",
                         "--reviews", review_name, *extra]
            def grounding_paths(base):
                return [base + "/" + value if value.endswith(".jsonl") else value for value in arguments]
            local = command([sys.executable, "-S", "-m", "evals_lab.grounding", *grounding_paths(folder.as_posix())], env=env)
            container = command(["docker", "run", "--rm", "--network", "none", "--read-only", "--mount", mount,
                                 "--entrypoint", "python", image, "-S", "-m", "evals_lab.grounding",
                                 *grounding_paths('/data')])
            if (local.returncode != expected_code or container.returncode != expected_code or
                    local.stdout != container.stdout or
                    local.stderr.replace(b"\r\n", b"\n") != container.stderr.replace(b"\r\n", b"\n")):
                raise RuntimeError("La evaluación de sustento difiere entre ejecución local y Docker.")
            if extra and json.loads(container.stdout)["review_unbound"] != 1:
                raise RuntimeError("La revisión antigua no quedó identificada sin vínculo.")
        for review_name, expected_code in (("completeness_reviews.jsonl", 0),
                                           ("completeness_empty_reviews.jsonl", 1),
                                           ("completeness_invalid_reviews.jsonl", 2)):
            arguments = ["--cases", "completeness_cases.jsonl", "--outputs", "completeness_outputs.jsonl",
                         "--reviews", review_name]
            def completeness_paths(base):
                return [base + "/" + value if value.endswith(".jsonl") else value for value in arguments]
            local = command([sys.executable, "-S", "-m", "evals_lab.completeness",
                             *completeness_paths(folder.as_posix())], env=env)
            container = command(["docker", "run", "--rm", "--network", "none", "--read-only", "--mount", mount,
                                 "--entrypoint", "python", image, "-S", "-m", "evals_lab.completeness",
                                 *completeness_paths('/data')])
            if (local.returncode != expected_code or container.returncode != expected_code or
                    local.stdout != container.stdout or
                    local.stderr.replace(b"\r\n", b"\n") != container.stderr.replace(b"\r\n", b"\n")):
                raise RuntimeError("La cobertura de información difiere entre ejecución local y Docker.")
            if expected_code == 0 and json.loads(container.stdout)["addressed_fraction"] != .5:
                raise RuntimeError("La imagen no calculó la cobertura esperada.")
        for case_name, expected_code in (("rag_grounding_cases.jsonl", 0), ("rag_grounding_bad_cases.jsonl", 2)):
            arguments = ["--report", "rag_report.json", "--cases", case_name]
            def rag_grounding_paths(base):
                return [base + "/" + value if value.endswith((".json", ".jsonl")) else value for value in arguments]
            local = command([sys.executable, "-S", "-m", "evals_lab.rag_grounding_adapter",
                             *rag_grounding_paths(folder.as_posix())], env=env)
            container = command(["docker", "run", "--rm", "--network", "none", "--read-only", "--mount", mount,
                                 "--entrypoint", "python", image, "-S", "-m", "evals_lab.rag_grounding_adapter",
                                 *rag_grounding_paths('/data')])
            if (local.returncode != expected_code or container.returncode != expected_code or
                    local.stdout != container.stdout or
                    local.stderr.replace(b"\r\n", b"\n") != container.stderr.replace(b"\r\n", b"\n")):
                raise RuntimeError("La conversión RAG para sustento difiere entre ejecución local y Docker.")
            if expected_code == 0:
                rows = [json.loads(line) for line in container.stdout.splitlines()]
                if [item["source_id"] for item in rows[0]["sources"]] != ["s1", "s2", "s3", "s4"]:
                    raise RuntimeError("La imagen no conservó los fragmentos RAG.")
        fixture = '''
import http.server, os, sys, threading
from evals_lab import live_metrics
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = b'{"status":"no_context","answer":"simulado","sources":[]}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-ID", "a" * 32)
        self.end_headers()
        self.wfile.write(body)
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
thread.start()
os.environ["RAG_READ_TOKEN"] = "synthetic_" + "x" * 32
try:
    code = live_metrics.main(["--cases", sys.argv[1], "--context", sys.argv[2], "--port", str(server.server_port), "--warmup", "1", "--limit", "2"])
finally:
    server.shutdown(); server.server_close(); thread.join()
raise SystemExit(code)
'''
        local = command([sys.executable, "-S", "-c", fixture, str(folder / "live_metrics_cases.jsonl"),
                         str(folder / "live_metrics_context.json")], env=env)
        container = command(["docker", "run", "--rm", "--network", "none", "--read-only", "--mount", mount,
                             "--entrypoint", "python", image, "-S", "-c", fixture,
                             "/data/live_metrics_cases.jsonl", "/data/live_metrics_context.json"])
        if local.returncode or container.returncode:
            raise RuntimeError("No se completó la medición simulada local o Docker.")
        local_report, docker_report = json.loads(local.stdout), json.loads(container.stdout)
        for key in ("cases_sha256", "context_sha256", "context", "run_status", "abort_reason"):
            if local_report[key] != docker_report[key]:
                raise RuntimeError("La medición simulada difiere entre local y Docker.")
        for report in (local_report, docker_report):
            measure = report["measurement"]
            if (report["warmup"]["attempted"] != 1 or measure["attempted"] != 2 or
                    measure["outcome_counts"]["ok_no_context"] != 2 or measure["failure_rate_observed"] != 0 or
                    measure["latency_ns"]["count"] != 2 or measure["latency_ns"]["p95_ns"] is not None):
                raise RuntimeError("La medición simulada no coincide con el contrato.")
        if b"Pregunta" in local.stdout + container.stdout or b"synthetic_" in local.stdout + container.stdout:
            raise RuntimeError("La medición simulada expuso preguntas o credenciales.")
        probe = ("import json,os; "
                 "files=sorted(os.path.relpath(os.path.join(d,f),'/app') for d,_,fs in os.walk('/app') for f in fs); "
                 "print(json.dumps({'uid':os.geteuid(),'files':files}))")
        result = command(["docker", "run", "--rm", "--network", "none", "--read-only", "--entrypoint", "python",
                          image, "-S", "-c", probe])
        expected_files = ["src/evals_lab/__init__.py", "src/evals_lab/__main__.py",
                          "src/evals_lab/cases.py", "src/evals_lab/completeness.py", "src/evals_lab/evaluate.py", "src/evals_lab/grounding.py", "src/evals_lab/live_metrics.py", "src/evals_lab/rag_adapter.py",
                          "src/evals_lab/rag_grounding_adapter.py",
                          "src/evals_lab/retrieval.py"]
        if result.returncode or json.loads(result.stdout) != {"uid": 10001, "files": expected_files}:
            raise RuntimeError("La imagen no cumple el usuario o contenido esperado.")
        print("Imagen verificada: códigos 0/1/2, paridad local y usuario sin privilegios.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python tests/docker_smoke.py NOMBRE_IMAGEN", file=sys.stderr)
        raise SystemExit(2)
    try:
        check(sys.argv[1])
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"No se completó la comprobación de la imagen: {exc}", file=sys.stderr)
        raise SystemExit(1)
