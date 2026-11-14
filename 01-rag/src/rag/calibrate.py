"""Experimento offline de umbrales sobre un corpus fijo y embeddings locales."""
import argparse
import json
import math
import os
import sys
from pathlib import Path

from .evaluate_api import load_cases
from .ingestion import ingest_text_file
from .ollama import OllamaError, embed
from .similarity import rank_vectors

IDS = ("fragmentacion", "embeddings", "persistencia")
THRESHOLDS = (0.4, 0.5, 0.6, 0.7, 0.8)


def analyze(cases, rankings, thresholds=THRESHOLDS):
    if len(cases) != len(rankings):
        raise ValueError("cada caso debe tener resultados")
    if any(not math.isfinite(t) or not -1 <= t <= 1 for t in thresholds):
        raise ValueError("umbrales inválidos")
    rows = []
    for threshold in thresholds:
        positive_hit = positive_no_context = negative_no_context = 0
        positives = sum(c["expected_document_id"] is not None for c in cases)
        negatives = len(cases) - positives
        for case, ranking in zip(cases, rankings):
            selected = [(doc, score) for doc, score in ranking if score >= threshold]
            expected = case["expected_document_id"]
            if expected is None:
                negative_no_context += not selected
            else:
                positive_no_context += not selected
                positive_hit += any(doc == expected for doc, _ in selected)
        rows.append({"threshold": threshold, "positive_hit": positive_hit,
                     "positive_no_context": positive_no_context, "positive_total": positives,
                     "negative_no_context": negative_no_context, "negative_total": negatives})
    return rows


def main():
    parser = argparse.ArgumentParser(description="Compara umbrales sin generar respuestas")
    parser.add_argument("--model", default=os.environ.get("OLLAMA_EMBED_MODEL"))
    parser.add_argument("--url", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--limit", type=int, default=2)
    args = parser.parse_args()
    if not args.model or not 1 <= args.limit <= len(IDS):
        parser.error("indica --model y un límite entre 1 y 3")
    root = Path(__file__).resolve().parents[2] / "eval_data"
    try:
        cases = load_cases(root / "calibration_cases.json")
        docs = []
        for name in IDS:
            chunks = ingest_text_file(root / (name + ".txt"), 500, 50)
            if len(chunks) != 1:
                raise ValueError("el experimento requiere un fragmento por documento")
            docs.append(chunks[0].text)
        vectors = embed(docs + [c["question"] for c in cases], model=args.model, base_url=args.url)
        doc_vectors = vectors[:len(docs)]
        rankings = [[(IDS[i], score) for i, score in rank_vectors(v, doc_vectors, args.limit)] for v in vectors[len(docs):]]
        report = {"model": args.model, "limit": args.limit, "thresholds": analyze(cases, rankings),
                  "cases": [{"id": c["id"], "expected_document_id": c["expected_document_id"],
                             "ranking": [{"document_id": doc, "score": score} for doc, score in ranking]}
                            for c, ranking in zip(cases, rankings)]}
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError, UnicodeError, OllamaError) as exc:
        print(f"Error del experimento: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
