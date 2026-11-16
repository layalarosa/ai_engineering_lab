"""Ejecuta integración contra un PostgreSQL Docker nuevo, aislado y efímero."""
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from memory_lab.postgres import initialize

IMAGE = "pgvector/pgvector:0.8.0-pg16"


def main():
    name = "memory-pgtest-" + secrets.token_hex(8)
    admin_password, runtime_password = secrets.token_hex(24), secrets.token_hex(24)
    docker_env = os.environ.copy()
    docker_env["POSTGRES_PASSWORD"] = admin_password
    started = False

    def docker(*args, check=True):
        result = subprocess.run(["docker", *args], env=docker_env, capture_output=True, timeout=90)
        if check and result.returncode:
            raise RuntimeError("Falló una operación Docker del laboratorio temporal.")
        return result

    try:
        docker("run", "--detach", "--rm", "--name", name, "--tmpfs", "/var/lib/postgresql/data",
               "--env", "POSTGRES_USER=memory_admin", "--env", "POSTGRES_DB=memory_test",
               "--env", "POSTGRES_PASSWORD", "--publish", "127.0.0.1::5432", IMAGE)
        started = True
        mapping = docker("port", name, "5432/tcp").stdout.decode("ascii").strip()
        if not mapping.startswith("127.0.0.1:"):
            raise RuntimeError("No se pudo determinar el puerto local temporal.")
        port = int(mapping.rsplit(":", 1)[1])
        admin_dsn = make_conninfo(host="127.0.0.1", port=port, dbname="memory_test", user="memory_admin", password=admin_password)
        runtime_dsn = make_conninfo(host="127.0.0.1", port=port, dbname="memory_test", user="memory_runtime", password=runtime_password)
        deadline = time.monotonic() + 60
        while True:
            ready = docker("exec", name, "pg_isready", "-U", "memory_admin", "-d", "memory_test", check=False)
            if ready.returncode == 0:
                try:
                    # pg_isready dentro del contenedor puede detectar el servidor
                    # transitorio de inicialización antes de abrir el puerto TCP.
                    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=2) as conn:
                        conn.execute("SELECT 1")
                    break
                except psycopg.OperationalError:
                    pass
            if time.monotonic() >= deadline:
                raise RuntimeError("PostgreSQL temporal no estuvo disponible.")
            time.sleep(0.5)
        initialize(admin_dsn)
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=10) as conn:
            conn.execute(sql.SQL("CREATE ROLE memory_runtime LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Literal(runtime_password)))
            with conn.transaction():
                conn.execute((ROOT / "ops" / "grant_runtime.sql").read_text(encoding="utf-8"))
        env = os.environ.copy()
        env["MEMORY_TEST_DATABASE_URL"] = runtime_dsn
        env["MEMORY_TEST_ADMIN_DATABASE_URL"] = admin_dsn
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"),
                                 "-p", "test_postgres_integration.py", "-v"], cwd=ROOT, env=env,
                                capture_output=True, timeout=240)
        report = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        for secret in (admin_password, runtime_password):
            report = report.replace(secret, "[secret]")
        print(report, end="")
        if result.returncode:
            raise RuntimeError("Fallaron las pruebas de integración de Memory.")
    finally:
        if started:
            result = docker("stop", "--time", "2", name, check=False)
            if result.returncode:
                raise RuntimeError("No se pudo detener el contenedor temporal " + name)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, psycopg.Error) as exc:
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(f"No se completó el laboratorio temporal de PostgreSQL: {detail}", file=sys.stderr)
        raise SystemExit(1)
