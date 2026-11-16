"""Comprueba la instantánea local anterior a crear la evaluación reservada."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("freeze.json")
FILES = (
    "docs/corrections-abstention-design.md",
    "experiments/corrections/DEVELOPMENT.md",
    "experiments/corrections/development.json",
    "experiments/corrections/development-results.json",
    "experiments/corrections/prototype.py",
    "experiments/corrections/run.py",
    "experiments/corrections/verify_freeze.py",
    "experiments/retrieval/run.py",
    "src/memory_lab/__init__.py",
    "src/memory_lab/memory.py",
    "tests/test_corrections_prototype.py",
)


def verify():
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        expected = data["sha256"]
        if data["version"] != 1 or not isinstance(expected, dict) or set(expected) != set(FILES):
            raise ValueError("Manifiesto inválido")
        changed = [name for name in FILES if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected[name]]
    except (OSError, ValueError, KeyError, TypeError):
        print("No se pudo verificar la instantánea congelada.")
        return False
    if changed:
        print("La instantánea cambió:")
        for name in changed:
            print(name)
        return False
    print("La instantánea de desarrollo coincide con los hashes registrados.")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if verify() else 1)
