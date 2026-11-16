"""Inicialización explícita y demostración de una sesión PostgreSQL sintética."""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from memory_lab.postgres import MemoryStorageError, PostgresConversationMemory, initialize, read_database_url


def main():
    parser = argparse.ArgumentParser(description="Laboratorio local de memoria persistente")
    parser.add_argument("action", choices=("init", "demo"))
    args = parser.parse_args()
    try:
        dsn = read_database_url()
        if args.action == "init":
            initialize(dsn)
            print("Esquema memory_lab inicializado.")
        else:
            memory = PostgresConversationMemory.create(dsn, uuid.uuid4())
            try:
                memory.append("user", "Uso Python")
                memory.append("assistant", "Entendido.")
                reopened = PostgresConversationMemory.open(dsn, memory.session_id)
                print(json.dumps(reopened.context(), ensure_ascii=True, indent=2))
            finally:
                memory.delete()
    except (ValueError, LookupError, MemoryStorageError) as exc:
        print(f"Error de Memory: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
