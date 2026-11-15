"""Empaqueta el servidor stdio con la biblioteca estándar de Python."""
import argparse
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Crear el paquete ejecutable de MCP")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "mcp_lab.pyz")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    source = ROOT / "src"
    zipapp.create_archive(source, args.output,
                          filter=lambda path: "__pycache__" not in path.parts and
                          ((source / path).is_dir() or path.suffix == ".py"))
    print(f"Paquete creado: {args.output}")


if __name__ == "__main__":
    main()
