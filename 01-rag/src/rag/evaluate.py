"""Evaluación pequeña de recuperación y registro de respuestas para revisión."""

import argparse
import json
import math
import os
import sys
from pathlib import Path

from .answer import MAX_QUESTION_CHARS, answer_question
from .ollama import OllamaError, embed
from . import openai_client
from .store import search


def load_cases(path):
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("el conjunto debe ser una lista no vacía")
    ids = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("cada caso debe ser un objeto")
        if any(not isinstance(case.get(key), str) or not case[key].strip() for key in ("id", "question", "expected_answer")):
            raise ValueError("cada caso necesita id, question y expected_answer")
        if len(case["question"]) > MAX_QUESTION_CHARS:
            raise ValueError("la pregunta excede el límite permitido")
        if "expected_source" not in case or (case["expected_source"] is not None and
                (not isinstance(case["expected_source"], str) or not case["expected_source"].strip())):
            raise ValueError("expected_source debe ser una ruta no vacía o null")
        if case["id"] in ids:
            raise ValueError("los identificadores deben ser únicos")
        ids.add(case["id"])
    return cases


def evaluate(cases, retrieve, *, limit, min_score=None, generate_answer=None):
    """Hit@k y MRR@k por fuente; los casos sin fuente se revisan por separado."""
    if not 1 <= limit <= 20 or (min_score is not None and (not math.isfinite(min_score) or not -1 <= min_score <= 1)):
        raise ValueError("límite o umbral inválido")
    records = []
    reciprocal_ranks = []
    for case in cases:
        results = retrieve(case["question"], limit)
        selected = [r for r in results if min_score is None or r.score >= min_score]
        expected = case["expected_source"]
        rank = next((i for i, result in enumerate(selected, 1)
                     if expected is not None and result.chunk.source == str(Path(expected))), None)
        if expected is not None:
            reciprocal_ranks.append(1 / rank if rank else 0.0)
        record = {"id": case["id"], "question": case["question"], "expected_source": expected,
                  "expected_answer": case["expected_answer"], "rank": rank,
                  "retrieved": [{"source": r.chunk.source, "index": r.chunk.index, "score": r.score,
                                 "text": r.chunk.text} for r in selected]}
        if generate_answer is not None:
            record["answer"] = generate_answer(case["question"], selected)
        records.append(record)
    count = len(reciprocal_ranks)
    return {"limit": limit, "min_score": min_score, "positive_cases": count,
            "hit_at_k": sum(rank > 0 for rank in reciprocal_ranks) / count if count else None,
            "mrr_at_k": sum(reciprocal_ranks) / count if count else None,
            "negative_cases": len(cases) - count, "cases": records}


def main():
    parser = argparse.ArgumentParser(description="Evalúa la recuperación y registra respuestas para revisión")
    parser.add_argument("cases", help="archivo JSON de casos")
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", choices=["ollama", "openai"], default="ollama")
    parser.add_argument("--generation-provider", choices=["ollama", "openai"], default=None)
    parser.add_argument("--generation-model", default=None, help="opcional; activa generación y revisión manual")
    parser.add_argument("--url", default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=None)
    args = parser.parse_args()
    args.model = args.model or os.getenv("OPENAI_EMBED_MODEL" if args.provider == "openai" else "OLLAMA_EMBED_MODEL")
    dsn = os.getenv("DATABASE_URL")
    if not dsn or not args.model:
        parser.error("configura DATABASE_URL y --model o OLLAMA_EMBED_MODEL")
    if args.provider == "ollama" and args.model.startswith("openai:"):
        parser.error("el prefijo openai: está reservado para vectores de OpenAI")
    embedding_fn = openai_client.embed if args.provider == "openai" else embed
    generation_fn = openai_client.generate if (args.generation_provider or args.provider) == "openai" else None
    model_key = f"openai:{args.model}" if args.provider == "openai" else args.model
    try:
        import psycopg
    except ImportError:
        print("Instala las dependencias con pip install -r requirements.txt", file=sys.stderr)
        return 1
    try:
        cases = load_cases(args.cases)
        with psycopg.connect(dsn, autocommit=True) as conn:
            def retrieve(question, limit):
                vector = embedding_fn([question], model=args.model, base_url=args.url)[0]
                return search(conn, model_key, vector, limit)

            def generate_answer(question, results):
                return answer_question(question, results, model=args.generation_model, base_url=args.url, generate_fn=generation_fn).text

            report = evaluate(cases, retrieve, limit=args.limit, min_score=args.min_score,
                              generate_answer=generate_answer if args.generation_model else None)
        report["embedding_model"] = args.model
        report["embedding_provider"] = args.provider
        report["generation_model"] = args.generation_model
        report["generation_provider"] = (args.generation_provider or args.provider) if args.generation_model else None
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    except (psycopg.Error, OllamaError, openai_client.OpenAIError, OSError, UnicodeError, ValueError) as exc:
        print(f"Error de evaluación: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
