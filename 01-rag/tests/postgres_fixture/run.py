"""Verifica permisos y restauración solo en un contenedor Docker nuevo y efímero."""
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
from rag.store import initialize
from check_db import check

IMAGE = "pgvector/pgvector:0.8.0-pg16"


def run():
    container = "rag-pgtest-" + secrets.token_hex(8)
    admin_password = secrets.token_hex(24)
    runtime_password = secrets.token_hex(24)
    started = False
    policy = (ROOT / "ops" / "grant_runtime.sql").read_text(encoding="utf-8")

    def docker(*args, input_text=None, expected_success=True):
        result = subprocess.run(["docker", *args], input=input_text, text=True, encoding="utf-8", capture_output=True, timeout=90)
        if expected_success and result.returncode != 0:
            operation = args[3] if args[:2] == ("exec", "-i") else (args[2] if args[0] == "exec" else args[0])
            raise RuntimeError(f"Falló la operación de prueba: {operation}")
        return result

    def psql(database, script, expected_success=True):
        return docker("exec", "-i", container, "psql", "-v", "ON_ERROR_STOP=1", "-U", "rag_admin", "-d", database,
                      input_text=script, expected_success=expected_success)

    try:
        docker("run", "-d", "--rm", "--name", container, "-e", "POSTGRES_USER=rag_admin", "-e", "POSTGRES_DB=rag_source",
               "-e", "POSTGRES_PASSWORD=" + admin_password, "-p", "127.0.0.1::5432", IMAGE)
        started = True
        deadline = time.monotonic() + 60
        while True:
            ready = docker("exec", container, "pg_isready", "-U", "rag_admin", "-d", "rag_source", expected_success=False)
            if ready.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("La base temporal no estuvo disponible")
            time.sleep(1)
        mapping = docker("port", container, "5432/tcp").stdout.strip()
        port = int(mapping.rsplit(":", 1)[1])
        admin_dsn = make_conninfo(host="127.0.0.1", port=port, dbname="rag_source", user="rag_admin", password=admin_password)
        def runtime(database):
            return make_conninfo(host="127.0.0.1", port=port, dbname=database, user="rag_runtime", password=runtime_password)
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=10) as conn:
            initialize(conn)
            conn.execute(sql.SQL("CREATE ROLE rag_runtime LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Literal(runtime_password)))
        psql("rag_source", policy)
        psql("rag_source", "GRANT UPDATE ON public.rag_models TO PUBLIC;\n")
        rejected = psql("rag_source", policy, expected_success=False)
        if rejected.returncode == 0 or "permisos adicionales efectivos" not in rejected.stderr:
            raise AssertionError("La política no detectó el permiso adicional de PUBLIC")
        psql("rag_source", "REVOKE UPDATE ON public.rag_models FROM PUBLIC;\n")
        check("source", runtime("rag_source"))
        docker("exec", container, "pg_dump", "-Fc", "-U", "rag_admin", "-d", "rag_source", "-f", "/tmp/rag-test.dump")
        docker("exec", container, "createdb", "-U", "rag_admin", "rag_restore")
        docker("exec", container, "pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "-U", "rag_admin", "-d", "rag_restore", "/tmp/rag-test.dump")
        psql("rag_restore", policy)
        check("restored", runtime("rag_restore"))
        print("Copia, permisos y restauración verificadas en bases temporales.")
    finally:
        if started:
            docker("stop", "--time", "2", container, expected_success=False)


if __name__ == "__main__":
    try:
        run()
    except (OSError, ValueError, RuntimeError, AssertionError, subprocess.SubprocessError, psycopg.Error) as exc:
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(f"Falló la verificación temporal ({detail}).", file=sys.stderr)
        raise SystemExit(1)
