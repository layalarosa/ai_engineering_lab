"""Evaluación de respuestas y recuperación a través de la API local."""
import argparse
import http.client
import json
import os
import re
import sys
from pathlib import Path

from .answer import MAX_QUESTION_CHARS


def load_cases(path):
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("se requiere una lista no vacía de casos")
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or any(not isinstance(case.get(k), str) or not case[k].strip() for k in ("id", "question", "expected_answer")):
            raise ValueError("cada caso requiere id, question y expected_answer")
        expected = case.get("expected_document_id")
        if "expected_document_id" not in case or (expected is not None and (not isinstance(expected, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", expected))):
            raise ValueError("expected_document_id debe ser un identificador válido o null")
        if case["id"] in ids or len(case["question"]) > MAX_QUESTION_CHARS:
            raise ValueError("identificador duplicado o pregunta demasiado larga")
        ids.add(case["id"])
    return cases


def evaluate(cases, ask, limit=3):
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("limit debe estar entre 1 y 20")
    records, ranks = [], []
    for case in cases:
        response = ask(case["question"], limit)
        if (not isinstance(response, dict) or response.get("status") not in ("answered", "no_context") or
            not isinstance(response.get("answer"), str) or not response["answer"].strip() or
            not isinstance(response.get("sources"), list) or
            any(not isinstance(s, dict) or not isinstance(s.get("document_id"), str) for s in response["sources"]) or
            len(response["sources"]) > limit or
            (response["status"] == "no_context") != (len(response["sources"]) == 0)):
            raise ValueError("respuesta de API inválida")
        expected = case["expected_document_id"]
        rank = next((i for i, source in enumerate(response["sources"], 1) if source["document_id"] == expected), None) if expected else None
        if expected is not None:
            ranks.append(1 / rank if rank else 0.0)
        records.append({"id": case["id"], "question": case["question"], "expected_document_id": expected,
                        "expected_answer": case["expected_answer"], "rank": rank,
                        "status": response["status"], "answer": response["answer"], "sources": response["sources"]})
    negative = [r for r in records if r["expected_document_id"] is None]
    return {"limit": limit, "positive_cases": len(ranks),
            "hit_at_k": sum(r > 0 for r in ranks) / len(ranks) if ranks else None,
            "mrr_at_k": sum(ranks) / len(ranks) if ranks else None,
            "negative_cases": len(negative), "negative_no_context": sum(r["status"] == "no_context" for r in negative),
            "cases": records}


def main():
    parser = argparse.ArgumentParser(description="Evalúa una API RAG local")
    parser.add_argument("cases")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    token = os.environ.get("RAG_READ_TOKEN", "")
    if not token or not token.isascii() or not 1 <= args.port <= 65535:
        parser.error("configura RAG_READ_TOKEN y un puerto válido")

    def ask(question, limit):
        conn = http.client.HTTPConnection("127.0.0.1", args.port, timeout=180)
        try:
            body = json.dumps({"question": question, "limit": limit}).encode("utf-8")
            conn.request("POST", "/v1/answers", body, {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            response = conn.getresponse()
            data = response.read(2_000_001)
            if response.status != 200 or len(data) > 2_000_000:
                raise ValueError(f"la API devolvió HTTP {response.status} o una respuesta demasiado grande")
            return json.loads(data)
        finally:
            conn.close()
    try:
        report = evaluate(load_cases(args.cases), ask, args.limit)
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError, UnicodeError, http.client.HTTPException) as exc:
        print(f"Error de evaluación: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
