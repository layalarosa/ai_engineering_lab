"""Orquesta clientes temporales desde tres IP y muestrea contenedores de prueba."""
import argparse
import http.client
import json
import re
import ssl
import subprocess
import threading
import time
from pathlib import Path

from load_probe import summarize


def summarize_resources(samples, services=("proxy", "api", "db", "model")):
    units = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3,
             "kB": 1000, "MB": 1000**2, "GB": 1000**3}
    result = {service: {"samples": 0, "peak_cpu_percent": None, "peak_memory_mib": None} for service in services}
    for sample in samples:
        for entry in sample.get("containers", []):
            service = next((name for name in services if entry["name"].endswith("-" + name + "-1")), None)
            if not service:
                continue
            match = re.match(r"\s*([\d.]+)\s*(KiB|MiB|GiB|kB|MB|GB|B)\s*/", entry["memory"])
            try:
                cpu = float(entry["cpu"].rstrip("%"))
                memory = float(match.group(1)) * units[match.group(2)] / (1024**2) if match else None
            except (ValueError, OverflowError):
                continue
            row = result[service]
            row["samples"] += 1
            row["peak_cpu_percent"] = max(row["peak_cpu_percent"] or 0, cpu)
            if memory is not None:
                row["peak_memory_mib"] = round(max(row["peak_memory_mib"] or 0, memory), 2)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ca", required=True)
    parser.add_argument("--port", type=int, default=18443)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"ragproxy[a-f0-9]{12}", args.project) or not 1 <= args.port <= 65535:
        parser.error("proyecto o puerto inválido")
    here = Path(__file__).resolve().parent
    compose = ["docker", "compose", "-p", args.project, "-f", str(here / "compose.yaml"), "-f", str(here / "compose.load.yaml")]
    context = ssl.create_default_context(cafile=args.ca)
    deadline = time.monotonic() + 20
    while True:
        conn = http.client.HTTPSConnection("localhost", args.port, context=context, timeout=10)
        try:
            conn.request("PUT", "/v1/documents/prueba", b"Un vector representa texto.",
                         {"Authorization": "Bearer test_write_token_only_000000000000000000", "Content-Type": "text/plain"})
            response = conn.getresponse()
            response.read()
            if response.status == 200:
                break
        except (OSError, http.client.HTTPException):
            pass
        finally:
            conn.close()
        if time.monotonic() >= deadline:
            raise RuntimeError("no se pudo preparar el documento de prueba")
        time.sleep(.25)

    container_ids = {}
    for service in ("proxy", "api", "db", "model"):
        output = subprocess.check_output(compose + ["ps", "-q", service], text=True, timeout=15).strip()
        if not output:
            raise RuntimeError("falta un contenedor de prueba")
        container_ids[service] = output
    stats, stop_stats = [], threading.Event()
    start_at = time.time() + 12

    def sample_stats():
        if stop_stats.wait(max(0, start_at - time.time())):
            return
        while not stop_stats.is_set():
            try:
                output = subprocess.check_output(["docker", "stats", "--no-stream", "--format", "{{json .}}", *container_ids.values()], text=True, timeout=15)
                entries = [json.loads(line) for line in output.splitlines() if line.strip()]
                stats.append({"seconds_since_start": round(time.time() - start_at, 2), "containers": [{"name": e["Name"], "cpu": e["CPUPerc"], "memory": e["MemUsage"]} for e in entries]})
            except (OSError, subprocess.SubprocessError, ValueError, KeyError):
                stats.append({"error": "no se pudo obtener una muestra"})
            stop_stats.wait(2)

    processes, names = [], []
    collector = threading.Thread(target=sample_stats, daemon=True)
    try:
        for index in range(3):
            name = f"{args.project}-load-{index}"
            names.append(name)
            command = ["docker", "run", "--rm", "--name", name, "--network", args.project + "_edge",
                       "--mount", f"type=bind,src={here},dst=/fixture,readonly",
                       "--mount", f"type=bind,src={Path(args.ca).resolve().parent},dst=/certs,readonly",
                       "python:3.12-slim", "python", "/fixture/sustained_client.py", "--start-at", str(start_at)]
            processes.append(subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        collector.start()
        clients = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=55)
            if process.returncode != 0:
                raise RuntimeError("falló un cliente de carga: " + stderr[-500:])
            clients.append(json.loads(stdout))
        if len({client["client_ip"] for client in clients}) != 3:
            raise AssertionError("los clientes no tienen tres direcciones distintas")
        records = [record for client in clients for record in client["records"]]
        result = summarize(records, 12)
        if result["status_counts"].get("200", 0) < 1 or result["status_counts"].get("503", 0) < 1:
            raise AssertionError("no se observaron éxitos y rechazos esperados")
        if result["status_counts"].get("invalid_response", 0) or result["status_counts"].get("connection_error", 0):
            raise AssertionError("la prueba observó respuestas inválidas o errores de conexión")
        stop_stats.set()
        collector.join(timeout=20)
        print(json.dumps({"duration_seconds": 12, "client_count": 3, "workers_per_client": 2,
                          "distinct_client_ips": len({c["client_ip"] for c in clients}), "aggregate": result,
                          "per_client": [{"client_ip": c["client_ip"], "summary": summarize(c["records"], 12)} for c in clients],
                          "resource_sample_errors": sum("error" in s for s in stats),
                          "resource_summary": summarize_resources(stats)}, indent=2))
    finally:
        stop_stats.set()
        if collector.is_alive():
            collector.join(timeout=20)
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
        for name in names:
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)


if __name__ == "__main__":
    main()
