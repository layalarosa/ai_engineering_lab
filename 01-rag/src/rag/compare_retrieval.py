"""Comparación didáctica de recuperación vectorial y mezcla léxica fija."""
import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

from .calibrate import IDS
from .evaluate_api import load_cases
from .ingestion import ingest_text_file
from .ollama import OllamaError, embed
from .similarity import cosine_similarity

# Peso fijado antes de ejecutar cualquiera de los dos conjuntos.
LEXICAL_WEIGHT = 0.2
STOPWORDS = frozenset("para entre como cual cuales que una uno los las del con por sin sus mas este esta desde sobre puede tienen deben cada dos".split())


def tokens(text):
    normalized = unicodedata.normalize("NFKD", text.casefold())
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    return {word for word in re.findall(r"[^\W_]+", normalized, re.UNICODE) if len(word) >= 3 and word not in STOPWORDS}


def rankings(question, query_vector, docs, vectors):
    """Calcula ambas clasificaciones sobre exactamente los mismos documentos."""
    if len(docs) != len(vectors) or not docs:
        raise ValueError("documentos y vectores deben tener el mismo tamaño no vacío")
    query_tokens = tokens(question)
    scored = []
    for (doc_id, text), vector in zip(docs, vectors):
        semantic = cosine_similarity(query_vector, vector)
        lexical = len(query_tokens & tokens(text)) / len(query_tokens) if query_tokens else 0.0
        hybrid = (1 - LEXICAL_WEIGHT) * semantic + LEXICAL_WEIGHT * lexical
        scored.append({"document_id": doc_id, "semantic": semantic, "lexical": lexical, "hybrid": hybrid})
    return {method: sorted(scored, key=lambda row: row[method], reverse=True) for method in ("semantic", "hybrid")}


def summarize(cases, case_rankings):
    if len(cases) != len(case_rankings):
        raise ValueError("se requiere una clasificación para cada caso")
    summary = {}
    for method in ("semantic", "hybrid"):
        ranks = []
        for case, ranking in zip(cases, case_rankings):
            expected = case["expected_document_id"]
            if expected is not None:
                rank = next((i for i, row in enumerate(ranking[method], 1) if row["document_id"] == expected), None)
                ranks.append(rank)
        n = len(ranks)
        summary[method] = {"positive_cases": n,
                           "hit_at_1": sum(r == 1 for r in ranks) / n if n else None,
                           "hit_at_2": sum(r is not None and r <= 2 for r in ranks) / n if n else None,
                           "mrr": sum(1 / r if r else 0 for r in ranks) / n if n else None}
    summary["negative_cases"] = sum(c["expected_document_id"] is None for c in cases)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Compara recuperación vectorial y léxica con un peso fijo")
    parser.add_argument("--model", default=os.environ.get("OLLAMA_EMBED_MODEL"))
    parser.add_argument("--url", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    args = parser.parse_args()
    if not args.model:
        parser.error("indica --model o OLLAMA_EMBED_MODEL")
    root = Path(__file__).resolve().parents[2] / "eval_data"
    try:
        calibration = load_cases(root / "calibration_cases.json")
        holdout = load_cases(root / "holdout_cases.json")
        if {c["id"] for c in calibration} & {c["id"] for c in holdout} or {c["question"].strip().casefold() for c in calibration} & {c["question"].strip().casefold() for c in holdout}:
            raise ValueError("los conjuntos de preguntas deben ser distintos")
        docs = []
        for doc_id in IDS:
            chunks = ingest_text_file(root / (doc_id + ".txt"), 500, 50)
            if len(chunks) != 1:
                raise ValueError("el experimento requiere un fragmento por documento")
            docs.append((doc_id, chunks[0].text))
        all_cases = calibration + holdout
        if any(c["expected_document_id"] is not None and c["expected_document_id"] not in IDS for c in all_cases):
            raise ValueError("fuente esperada fuera del corpus")
        vectors = embed([text for _, text in docs] + [c["question"] for c in all_cases], model=args.model, base_url=args.url)
        all_rankings = [rankings(c["question"], v, docs, vectors[:len(docs)]) for c, v in zip(all_cases, vectors[len(docs):])]
        report = {"model": args.model, "lexical_weight": LEXICAL_WEIGHT, "corpus_size": len(docs), "sets": {}}
        for name, cases, values in (("calibration", calibration, all_rankings[:len(calibration)]), ("holdout", holdout, all_rankings[len(calibration):])):
            report["sets"][name] = {"metrics": summarize(cases, values),
                                    "cases": [{"id": c["id"], "expected_document_id": c["expected_document_id"],
                                               "semantic": [r["document_id"] for r in rank["semantic"]],
                                               "hybrid": [r["document_id"] for r in rank["hybrid"]]}
                                              for c, rank in zip(cases, values)]}
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError, UnicodeError, OllamaError) as exc:
        print(f"Error de comparación: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
