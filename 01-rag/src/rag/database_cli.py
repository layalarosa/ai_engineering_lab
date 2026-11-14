"""Comandos para indexar y consultar documentos persistidos."""

import argparse
import os
import sys
from pathlib import Path

from .answer import MAX_QUESTION_CHARS, answer_question
from .ingestion import ingest_text_file
from .ollama import OllamaError, embed
from .observability import Trace
from . import openai_client
from .store import initialize, replace_document, search


def main():
    parser = argparse.ArgumentParser(description="Índice RAG en PostgreSQL")
    parser.add_argument("action", choices=["init", "index", "search", "ask"])
    parser.add_argument("value", nargs="?", help="archivo para index o consulta para search/ask")
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", choices=["ollama", "openai"], default="ollama")
    parser.add_argument("--generation-provider", choices=["ollama", "openai"], default=None)
    parser.add_argument("--url", default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--generation-model", default=None)
    parser.add_argument("--min-score", type=float, default=None, help="umbral opcional de similitud para ask")
    parser.add_argument("--observe", action="store_true", help="emite eventos operativos JSON en stderr")
    args = parser.parse_args()
    generation_provider = args.generation_provider or args.provider
    args.model = args.model or os.getenv("OPENAI_EMBED_MODEL" if args.provider == "openai" else "OLLAMA_EMBED_MODEL")
    args.generation_model = args.generation_model or os.getenv("OPENAI_GENERATION_MODEL" if generation_provider == "openai" else "OLLAMA_GENERATION_MODEL")
    embedding_fn = openai_client.embed if args.provider == "openai" else embed
    generation_fn = openai_client.generate if generation_provider == "openai" else None
    model_key = f"openai:{args.model}" if args.provider == "openai" else args.model
    trace = Trace(enabled=args.observe)
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        parser.error("configura DATABASE_URL")
    if args.action != "init" and (not args.value or not args.model):
        parser.error("index, search y ask requieren un valor y --model o OLLAMA_EMBED_MODEL")
    if args.provider == "ollama" and args.model and args.model.startswith("openai:"):
        parser.error("el prefijo openai: está reservado para vectores de OpenAI")
    if args.action == "ask" and not args.generation_model:
        parser.error("ask requiere --generation-model o OLLAMA_GENERATION_MODEL")
    try:
        import psycopg
    except ImportError:
        print("Instala las dependencias con pip install -r requirements.txt", file=sys.stderr)
        return 1
    try:
        with trace.step("connect_db"):
            conn = psycopg.connect(dsn, autocommit=True)
        with conn:
            if args.action == "init":
                with trace.step("initialize"):
                    initialize(conn)
                print("Esquema inicializado.")
            elif args.action == "index":
                with trace.step("ingest"):
                    chunks = ingest_text_file(args.value, 500, 50)
                with trace.step("embed", provider=args.provider):
                    vectors = embedding_fn([c.text for c in chunks], model=args.model, base_url=args.url)
                with trace.step("persist"):
                    replace_document(conn, model_key, str(Path(args.value)), chunks, vectors)
                print(f"Indexados {len(chunks)} fragmentos.")
            else:
                if not args.value.strip() or len(args.value) > MAX_QUESTION_CHARS or not 1 <= args.limit <= 20:
                    raise ValueError("consulta o límite inválido")
                with trace.step("embed", provider=args.provider):
                    vector = embedding_fn([args.value], model=args.model, base_url=args.url)[0]
                with trace.step("retrieve"):
                    results = search(conn, model_key, vector, args.limit)
                if args.action == "search":
                    for result in results:
                        print(f"[{result.chunk.source} #{result.chunk.index}, similitud={result.score:.4f}]\n{result.chunk.text}\n")
                else:
                    # La etapa mide preparación, filtrado y generación; puede no llamar al modelo
                    # cuando no hay contexto que supere el umbral.
                    with trace.step("answer", provider=generation_provider):
                        answer = answer_question(args.value, results, model=args.generation_model, base_url=args.url, min_score=args.min_score, generate_fn=generation_fn)
                    print(answer.text)
                    if answer.sources:
                        print("\nFragmentos recuperados:")
                        for i, result in enumerate(answer.sources, 1):
                            print(f"[{i}] {result.chunk.source} #{result.chunk.index} (similitud={result.score:.4f})\n{result.chunk.text}\n")
    except (psycopg.Error, OllamaError, openai_client.OpenAIError, OSError, UnicodeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
