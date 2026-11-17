"""Agrega revisiones offline de cobertura de puntos esperados."""
import argparse
import json
import os
import sys

from .cases import InputError, _read_jsonl
from .grounding import SHA256, _id, _items, _object, _span, _text, _validate_output

VERDICTS = ("addressed", "omitted", "uncertain")


def _validate_case(record):
    _id(record["id"])
    _text(record["input"])
    _items(record["expected_items"])
    seen = set()
    for item in record["expected_items"]:
        _object(item, {"item_id", "description"})
        _id(item["item_id"])
        _text(item["description"], nonblank=True)
        if item["item_id"] in seen:
            raise ValueError
        seen.add(item["item_id"])


def _validate_review(record):
    _id(record["id"])
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise ValueError
    for key in ("case_sha256", "response_sha256"):
        if not isinstance(record[key], str) or SHA256.fullmatch(record[key]) is None:
            raise ValueError
    if record["review_status"] not in ("complete", "incomplete"):
        raise ValueError
    _items(record["items"])
    seen = set()
    for item in record["items"]:
        _object(item, {"item_id", "verdict", "answer_spans"})
        _id(item["item_id"])
        if item["item_id"] in seen or item["verdict"] not in VERDICTS:
            raise ValueError
        seen.add(item["item_id"])
        _items(item["answer_spans"])
        if ((item["verdict"] == "addressed" and not item["answer_spans"]) or
                (item["verdict"] == "omitted" and item["answer_spans"])):
            raise ValueError
        for span in item["answer_spans"]:
            _span(span)


def load_inputs(cases_path, outputs_path, reviews_path):
    """Valida las tres instantáneas y sus referencias antes de agregarlas."""
    case_line_hashes, output_line_hashes = {}, {}
    cases, cases_hash = _read_jsonl(cases_path, kind="casos", keys={"id", "input", "expected_items"},
                                    allow_empty=False, validate=_validate_case, line_hashes=case_line_hashes)
    outputs, outputs_hash = _read_jsonl(outputs_path, kind="respuestas", keys={"id", "answer", "sources"},
                                        allow_empty=True, validate=_validate_output, allow_integers=True,
                                        line_hashes=output_line_hashes)
    reviews, reviews_hash = _read_jsonl(reviews_path, kind="revisiones",
                                        keys={"id", "schema_version", "case_sha256", "response_sha256", "review_status", "items"},
                                        allow_empty=True, validate=_validate_review, allow_integers=True)
    cases_by_id = {case["id"]: case for case in cases}
    outputs_by_id = {output["id"]: output for output in outputs}
    if any(row["id"] not in cases_by_id for row in outputs + reviews):
        raise InputError("Hay IDs que no existen en los casos.")
    for review in reviews:
        case_id = review["id"]
        if case_id not in outputs_by_id:
            raise InputError("Hay revisiones sin respuesta.")
        if review["case_sha256"] != case_line_hashes[case_id]:
            raise InputError("La revisión no corresponde al caso registrado.")
        if review["response_sha256"] != output_line_hashes[case_id]:
            raise InputError("La revisión no corresponde a la respuesta registrada.")
        expected = {item["item_id"] for item in cases_by_id[case_id]["expected_items"]}
        reviewed = {item["item_id"] for item in review["items"]}
        if not expected or not reviewed <= expected or (review["review_status"] == "complete" and reviewed != expected):
            raise InputError("Los juicios no corresponden a los puntos esperados.")
        answer = outputs_by_id[case_id]["answer"]
        if any(span["end"] > len(answer) for item in review["items"] for span in item["answer_spans"]):
            raise InputError("Intervalo de respuesta inválido.")
    return cases, outputs, reviews, cases_hash, outputs_hash, reviews_hash


def evaluate(cases, outputs, reviews, *, cases_sha256, outputs_sha256, reviews_sha256):
    """Agrega juicios previamente validados sin repetir textos de entrada."""
    outputs_by_id = {item["id"]: item for item in outputs}
    reviews_by_id = {item["id"]: item for item in reviews}
    counts = {"missing_output": 0, "unlabeled": 0, "review_missing": 0,
              "review_incomplete": 0, "review_complete": 0,
              "addressed": 0, "omitted": 0, "uncertain": 0, "items_excluded_incomplete": 0}
    unlabeled_total = sum(not case["expected_items"] for case in cases)
    eligible = sum(bool(case["expected_items"]) and case["id"] in outputs_by_id for case in cases)
    details = []
    for case in cases:
        case_id = case["id"]
        review = reviews_by_id.get(case_id)
        items, all_addressed = [], None
        if case_id not in outputs_by_id:
            status = "missing_output"
        elif not case["expected_items"]:
            status = "unlabeled"
        elif review is None:
            status = "review_missing"
        else:
            status = "review_" + review["review_status"]
            items = [{"item_id": item["item_id"], "verdict": item["verdict"], "answer_spans": item["answer_spans"]}
                     for item in review["items"]]
            if status == "review_incomplete":
                counts["items_excluded_incomplete"] += len(items)
            else:
                for item in items:
                    counts[item["verdict"]] += 1
                verdicts = {item["verdict"] for item in items}
                if "omitted" in verdicts:
                    all_addressed = False
                elif "uncertain" not in verdicts:
                    all_addressed = True
        counts[status] += 1
        details.append({"id": case_id, "status": status, "has_expected_items": bool(case["expected_items"]),
                        "items": items, "all_addressed": all_addressed})
    total, present = len(cases), len(outputs)
    scored = counts["addressed"] + counts["omitted"] + counts["uncertain"]
    decided = counts["addressed"] + counts["omitted"]
    return {"schema_version": 1, "evaluation_type": "completeness", "cases_sha256": cases_sha256,
            "outputs_sha256": outputs_sha256, "reviews_sha256": reviews_sha256,
            "total_cases": total, "outputs_present": present, "unlabeled_cases_total": unlabeled_total,
            "eligible_cases": eligible, **counts, "total_items_scored": scored,
            "output_coverage": present / total, "review_coverage": counts["review_complete"] / eligible if eligible else None,
            "addressed_fraction": counts["addressed"] / scored if scored else None,
            "addressed_fraction_decided": counts["addressed"] / decided if decided else None, "cases": details}


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise InputError("Argumentos inválidos; usa --cases, --outputs y --reviews.")


def main(argv=None):
    parser = _Parser(prog="evals_lab.completeness", description="Agrega revisiones de cobertura registradas.",
                     allow_abbrev=False)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--outputs", required=True)
    parser.add_argument("--reviews", required=True)
    try:
        args = parser.parse_args(argv)
        cases, outputs, reviews, cases_hash, outputs_hash, reviews_hash = load_inputs(args.cases, args.outputs, args.reviews)
        report = evaluate(cases, outputs, reviews, cases_sha256=cases_hash,
                          outputs_sha256=outputs_hash, reviews_sha256=reviews_hash)
        encoded = (json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                              allow_nan=False) + "\n").encode("ascii")
        if sys.stdout.buffer.write(encoded) != len(encoded):
            raise OSError("incomplete output")
        sys.stdout.buffer.flush()
        return 1 if any(report[key] for key in ("missing_output", "unlabeled_cases_total", "review_missing",
                                                "review_incomplete", "uncertain")) else 0
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
