"""Ejecuta una búsqueda local sobre un documento UTF-8."""

import argparse
import os
import sys

from .ollama import OllamaError
from .search import search_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Busca fragmentos con embeddings de Ollama")
    parser.add_argument("documento")
    parser.add_argument("consulta")
    parser.add_argument("--model", default=os.environ.get("OLLAMA_EMBED_MODEL"), help="modelo de embeddings; también OLLAMA_EMBED_MODEL")
    parser.add_argument("--url", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    if not args.model:
        parser.error("indica --model o configura OLLAMA_EMBED_MODEL")
    try:
        results = search_file(args.documento, args.consulta, model=args.model, base_url=args.url, limit=args.limit)
    except (OllamaError, OSError, UnicodeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    for result in results:
        print(f"[{result.chunk.source} #{result.chunk.index}, similitud={result.score:.4f}]\n{result.chunk.text}\n")
    if not results:
        print("El documento no contiene fragmentos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
