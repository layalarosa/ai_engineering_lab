"""Carga pequeña y fallos contra el Compose de pruebas, nunca contra servicios externos."""
import argparse
import http.client
import json
import math
import re
import ssl
import subprocess
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

READ = "test_read_token_only_0000000000000000000"
WRITE = "test_write_token_only_000000000000000000"


def quantile(values, percentile):
    if not values:
        return None
    return round(sorted(values)[max(0, math.ceil(percentile * len(values)) - 1)], 2)


def summarize(records, wall_seconds):
    counts = Counter(str(row["status"]) for row in records)
    successful = [row["elapsed_ms"] for row in records if row["status"] == 200]
    return {"attempts": len(records), "status_counts": dict(sorted(counts.items())),
            "elapsed_seconds": round(wall_seconds, 3),
            "completed_per_second_over_burst": round(len(successful) / wall_seconds, 3) if wall_seconds else None,
            "successful_latency_ms": {"sample_count": len(successful), "p50": quantile(successful, .50),
                                      "p95": quantile(successful, .95) if len(successful) >= 20 else None,
                                      "p99": quantile(successful, .99) if len(successful) >= 100 else None}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ca", required=True)
    parser.add_argument("--port", type=int, default=18443)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"ragproxy[a-f0-9]{12}", args.project) or not 1 <= args.port <= 65535:
        parser.error("se requiere un proyecto de prueba y puerto válidos")
    here = Path(__file__).resolve().parent
    compose = ["docker", "compose", "-p", args.project, "-f", str(here / "compose.yaml"), "-f", str(here / "compose.load.yaml")]
    context = ssl.create_default_context(cafile=args.ca)

    def request(method="POST", path="/v1/answers", token=READ, body=b'{"question":"vector"}', media="application/json"):
        started = time.monotonic()
        conn = http.client.HTTPSConnection("localhost", args.port, context=context, timeout=20)
        try:
            conn.request(method, path, body, {"Authorization": "Bearer " + token, "Content-Type": media})
            response = conn.getresponse()
            data = response.read()
            status = response.status
            if status == 200 and path == "/v1/answers":
                try:
                    answer = json.loads(data)
                    if answer.get("status") != "answered" or not any(s.get("document_id") == "prueba" for s in answer.get("sources", [])):
                        status = "invalid_response"
                except (ValueError, AttributeError, TypeError):
                    status = "invalid_response"
            return {"status": status, "elapsed_ms": (time.monotonic() - started) * 1000}
        except (OSError, http.client.HTTPException):
            return {"status": "connection_error", "elapsed_ms": (time.monotonic() - started) * 1000}
        finally:
            conn.close()

    def wait_success(timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = request()
            if result["status"] == 200:
                return result
            time.sleep(.5)
        raise RuntimeError("el servicio no recuperó disponibilidad")

    # Una llamada inicial prueba que el modelo simulado devuelve contexto a la consulta.
    deadline = time.monotonic() + 20
    while True:
        seed = request("PUT", "/v1/documents/prueba", WRITE, b"Un vector representa texto.", "text/plain; charset=utf-8")
        if seed["status"] == 200:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(f"no se pudo indexar el documento sintético: {seed['status']}")
        time.sleep(.5)

    groups = []
    for concurrency in (1, 2, 4, 6):
        barrier = threading.Barrier(concurrency)
        def worker():
            barrier.wait(timeout=10)
            return request()
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            records = list(pool.map(lambda _: worker(), range(concurrency)))
        summary = summarize(records, time.monotonic() - started)
        summary["concurrency"] = concurrency
        groups.append(summary)
    if groups[0]["status_counts"].get("200") != 1 or groups[1]["status_counts"].get("200") != 2:
        raise AssertionError("el servicio no completó la carga base")
    if not any(g["status_counts"].get("503", 0) for g in groups[2:]):
        raise AssertionError("no se observó el rechazo esperado por concurrencia")

    failures = {}
    for service in ("model", "db"):
        subprocess.run(compose + ["stop", service], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        try:
            result = request()
            failures[service] = {"during_stop": result["status"], "outage_response_ms": round(result["elapsed_ms"], 2)}
            if result["status"] != 503:
                raise AssertionError(f"la caída de {service} no produjo 503: {result['status']}")
        finally:
            restart_started = time.monotonic()
            subprocess.run(compose + ["start", service], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        failures[service]["after_restart"] = wait_success()["status"]
        failures[service]["time_to_success_seconds"] = round(time.monotonic() - restart_started, 3)

    print(json.dumps({"provider": "simulated", "provider_delay_seconds_per_call": 0.4,
                      "proxy_limit_per_ip": 2, "proxy_global_limit": 4,
                      "groups": groups, "faults": failures}, indent=2))


if __name__ == "__main__":
    main()
