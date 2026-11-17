"""Evaluación offline de recuperación a nivel de documento único."""
import argparse
import json
import os
import re
import sys

from .cases import ID_PATTERN, MAX_TEXT_CHARS, InputError, _read_jsonl

DOCUMENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
MAX_DOCUMENTS = 20


def _document_id(value):
    if not isinstance(value, str) or DOCUMENT_ID.fullmatch(value) is None:
        raise ValueError


def _case_id(value):
    if not isinstance(value, str) or ID_PATTERN.fullmatch(value) is None:
        raise ValueError


def _document_list(value):
    if not isinstance(value, list) or len(value) > MAX_DOCUMENTS:
        raise ValueError
    for item in value:
        _document_id(item)
    if len(set(value)) != len(value):
        raise ValueError


def _validate_corpus(record):
    _document_id(record["document_id"])


def _validate_case(record):
    _case_id(record["id"])
    value = record["input"]
    if not isinstance(value, str) or len(value) > MAX_TEXT_CHARS:
        raise ValueError
    value.encode("utf-8")
    _document_list(record["relevant_document_ids"])


def _validate_output(record):
    _case_id(record["id"])
    _document_list(record["retrieved_document_ids"])


def load_inputs(corpus_path, cases_path, outputs_path):
    corpus, corpus_hash = _read_jsonl(corpus_path, kind="corpus", keys={"document_id"},
                                      allow_empty=False, id_field="document_id", validate=_validate_corpus)
    cases, cases_hash = _read_jsonl(cases_path, kind="casos", keys={"id", "input", "relevant_document_ids"},
                                    allow_empty=False, validate=_validate_case)
    outputs, outputs_hash = _read_jsonl(outputs_path, kind="respuestas", keys={"id", "retrieved_document_ids"},
                                        allow_empty=True, validate=_validate_output)
    document_ids = {item["document_id"] for item in corpus}
    case_ids = {item["id"] for item in cases}
    if any(not set(item["relevant_document_ids"]) <= document_ids for item in cases):
        raise InputError("Hay documentos relevantes que no existen en el corpus.")
    if any(item["id"] not in case_ids for item in outputs):
        raise InputError("Hay respuestas con IDs que no existen en los casos.")
    if any(not set(item["retrieved_document_ids"]) <= document_ids for item in outputs):
        raise InputError("Hay documentos recuperados que no existen en el corpus.")
    return corpus_hash, cases, cases_hash, outputs, outputs_hash


def evaluate(cases, outputs, *, k, corpus_sha256, dataset_sha256, outputs_sha256):
    """Calcula métricas sobre entradas previamente validadas."""
    if type(k) is not int or not 1 <= k <= MAX_DOCUMENTS:
        raise InputError("El valor de k debe ser un entero entre 1 y 20.")
    by_id = {item["id"]: item["retrieved_document_ids"] for item in outputs}
    positive = {"total": 0, "evaluated": 0, "missing": 0, "coverage": None,
                "hit_at_k": None, "mrr_at_k": None}
    negative = {"total": 0, "evaluated": 0, "missing": 0, "coverage": None,
                "empty_returned": 0, "nonempty_returned": 0, "negative_nonempty_rate": None}
    counts = {"passed": 0, "failed": 0, "errors": 0}
    details, hits, reciprocal_sum = [], 0, 0.0
    for case in cases:
        relevant = set(case["relevant_document_ids"])
        group = "positive" if relevant else "negative"
        stats = positive if relevant else negative
        stats["total"] += 1
        rank = None
        if case["id"] not in by_id:
            stats["missing"] += 1
            status, error_code = "error", "missing_response"
            counts["errors"] += 1
        else:
            stats["evaluated"] += 1
            retrieved = by_id[case["id"]][:k]
            if relevant:
                rank = next((index for index, doc in enumerate(retrieved, 1) if doc in relevant), None)
                if rank is not None:
                    hits += 1
                    reciprocal_sum += 1 / rank
                passed = rank is not None
            else:
                passed = not retrieved
                stats["empty_returned" if passed else "nonempty_returned"] += 1
            status, error_code = ("passed" if passed else "failed"), None
            counts[status] += 1
        details.append({"id": case["id"], "group": group, "status": status,
                        "rank": rank, "error_code": error_code})
    for stats in (positive, negative):
        if stats["total"]:
            stats["coverage"] = stats["evaluated"] / stats["total"]
    if positive["evaluated"]:
        positive["hit_at_k"] = hits / positive["evaluated"]
        positive["mrr_at_k"] = reciprocal_sum / positive["evaluated"]
    if negative["evaluated"]:
        negative["negative_nonempty_rate"] = negative["nonempty_returned"] / negative["evaluated"]
    return {"schema_version": 1, "evaluation_type": "retrieval", "k": k,
            "corpus_sha256": corpus_sha256, "dataset_sha256": dataset_sha256, "outputs_sha256": outputs_sha256,
            "total": len(cases), **counts, "positive": positive, "negative": negative, "cases": details}


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise InputError("Argumentos inválidos; usa --corpus, --cases, --outputs y --k.")


def main(argv=None):
    parser = _Parser(prog="evals_lab.retrieval", description="Evalúa listas de documentos recuperados.",
                     allow_abbrev=False)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--outputs", required=True)
    parser.add_argument("--k", type=int, default=3)
    try:
        args = parser.parse_args(argv)
        if not 1 <= args.k <= MAX_DOCUMENTS:
            raise InputError("El valor de k debe ser un entero entre 1 y 20.")
        corpus_hash, cases, cases_hash, outputs, outputs_hash = load_inputs(args.corpus, args.cases, args.outputs)
        report = evaluate(cases, outputs, k=args.k, corpus_sha256=corpus_hash,
                          dataset_sha256=cases_hash, outputs_sha256=outputs_hash)
        encoded = (json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                              allow_nan=False) + "\n").encode("ascii")
        if sys.stdout.buffer.write(encoded) != len(encoded):
            raise OSError("incomplete output")
        sys.stdout.buffer.flush()
        return 0 if report["failed"] == report["errors"] == 0 else 1
    except BrokenPipeError:
        try:
            null_fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(null_fd, sys.stdout.fileno())
            finally:
                os.close(null_fd)
        except OSError:
            pass
        print("Error de evaluación: no se pudo escribir el informe.", file=sys.stderr)
        return 2
    except InputError as exc:
        print(f"Error de evaluación: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("Error de evaluación: no se pudo completar la evaluación.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
