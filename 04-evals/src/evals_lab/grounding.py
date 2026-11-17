"""Agrega anotaciones humanas offline sobre el sustento de afirmaciones."""
import argparse
import json
import os
import re
import sys

from .cases import ID_PATTERN, MAX_TEXT_CHARS, InputError, _read_jsonl
from .retrieval import DOCUMENT_ID

MAX_ITEMS = 20
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
RELATIONS = ("supports", "contradicts", "context")
LEGACY_REVIEW_KEYS = {"id", "response_sha256", "review_status", "abstention", "claims"}
REVIEW_KEYS = LEGACY_REVIEW_KEYS | {"schema_version", "case_sha256"}


def _object(value, keys):
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError


def _id(value, pattern=ID_PATTERN):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError


def _text(value, *, nonblank=False):
    if not isinstance(value, str) or len(value) > MAX_TEXT_CHARS or (nonblank and not value.strip()):
        raise ValueError
    value.encode("utf-8")


def _items(value):
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise ValueError


def _span(value):
    _object(value, {"start", "end"})
    if (type(value["start"]) is not int or type(value["end"]) is not int or
            not 0 <= value["start"] < value["end"] <= MAX_TEXT_CHARS):
        raise ValueError


def _validate_case(record):
    _id(record["id"])
    _text(record["input"])


def _validate_output(record):
    _id(record["id"])
    _text(record["answer"])
    _items(record["sources"])
    seen = set()
    for source in record["sources"]:
        _object(source, {"source_id", "document_id", "chunk_index", "text"})
        _id(source["source_id"])
        _id(source["document_id"], DOCUMENT_ID)
        if type(source["chunk_index"]) is not int or not 0 <= source["chunk_index"] <= 999:
            raise ValueError
        _text(source["text"])
        if source["source_id"] in seen:
            raise ValueError
        seen.add(source["source_id"])


def _validate_review(record):
    if set(record) == REVIEW_KEYS:
        if type(record["schema_version"]) is not int or record["schema_version"] != 2:
            raise ValueError
        if not isinstance(record["case_sha256"], str) or SHA256.fullmatch(record["case_sha256"]) is None:
            raise ValueError
    elif set(record) != LEGACY_REVIEW_KEYS:
        raise ValueError
    _id(record["id"])
    if not isinstance(record["response_sha256"], str) or SHA256.fullmatch(record["response_sha256"]) is None:
        raise ValueError
    if record["review_status"] not in ("complete", "incomplete"):
        raise ValueError
    if type(record["abstention"]) is not bool and not (record["review_status"] == "incomplete" and record["abstention"] is None):
        raise ValueError
    _items(record["claims"])
    seen = set()
    for claim in record["claims"]:
        _object(claim, {"claim_id", "claim", "answer_span", "evidence"})
        _id(claim["claim_id"])
        if claim["claim_id"] in seen:
            raise ValueError
        seen.add(claim["claim_id"])
        _text(claim["claim"], nonblank=True)
        _span(claim["answer_span"])
        _items(claim["evidence"])
        for evidence in claim["evidence"]:
            _object(evidence, {"source_id", "start", "end", "relation"})
            _id(evidence["source_id"])
            _span({"start": evidence["start"], "end": evidence["end"]})
            if evidence["relation"] not in RELATIONS:
                raise ValueError


def load_inputs(cases_path, outputs_path, reviews_path, *, allow_unbound_reviews=False):
    """Lee y valida referencias cruzadas antes de devolver datos evaluables."""
    case_line_hashes = {}
    cases, cases_hash = _read_jsonl(cases_path, kind="casos", keys={"id", "input"},
                                    allow_empty=False, validate=_validate_case, line_hashes=case_line_hashes)
    line_hashes = {}
    outputs, outputs_hash = _read_jsonl(outputs_path, kind="respuestas", keys={"id", "answer", "sources"},
                                        allow_empty=True, validate=_validate_output,
                                        allow_integers=True, line_hashes=line_hashes)
    reviews, reviews_hash = _read_jsonl(reviews_path, kind="revisiones",
                                        keys=REVIEW_KEYS, allow_empty=True, validate=_validate_review,
                                        allow_integers=True,
                                        alternative_keys=(LEGACY_REVIEW_KEYS,) if allow_unbound_reviews else ())
    cases_by_id = {row["id"] for row in cases}
    outputs_by_id = {row["id"]: row for row in outputs}
    if any(row["id"] not in cases_by_id for row in outputs + reviews):
        raise InputError("Hay IDs que no existen en los casos.")
    for review in reviews:
        case_id = review["id"]
        if case_id not in outputs_by_id:
            raise InputError("Hay revisiones sin respuesta.")
        if "schema_version" in review and review["case_sha256"] != case_line_hashes[case_id]:
            raise InputError("La revisión no corresponde al caso registrado.")
        if review["response_sha256"] != line_hashes[case_id]:
            raise InputError("La revisión no corresponde a la respuesta registrada.")
        output = outputs_by_id[case_id]
        sources = {source["source_id"]: source["text"] for source in output["sources"]}
        for claim in review["claims"]:
            if claim["answer_span"]["end"] > len(output["answer"]):
                raise InputError("Intervalo de respuesta inválido.")
            for evidence in claim["evidence"]:
                if evidence["source_id"] not in sources or evidence["end"] > len(sources[evidence["source_id"]]):
                    raise InputError("Referencia de evidencia inválida.")
    return cases, outputs, reviews, cases_hash, outputs_hash, reviews_hash


def _verdict(evidence):
    relations = {item["relation"] for item in evidence}
    if "supports" in relations and "contradicts" in relations:
        return "conflicting"
    if "supports" in relations:
        return "supported"
    if "contradicts" in relations:
        return "contradicted"
    return "insufficient"


def evaluate(cases, outputs, reviews, *, cases_sha256, outputs_sha256, reviews_sha256):
    """Agrega revisiones previamente validadas sin repetir textos sensibles."""
    outputs_by_id = {item["id"]: item for item in outputs}
    reviews_by_id = {item["id"]: item for item in reviews}
    counts = {"missing_output": 0, "review_complete": 0, "review_incomplete": 0, "review_missing": 0,
              "review_unbound": 0, "claims_excluded_unbound": 0,
              "complete_no_claims": 0, "explicit_abstentions": 0, "claims_excluded_incomplete": 0,
              "supported": 0, "contradicted": 0, "conflicting": 0, "insufficient": 0}
    details = []
    for case in cases:
        case_id = case["id"]
        review = reviews_by_id.get(case_id)
        case_binding = declared_review_status = None
        if case_id not in outputs_by_id:
            status, claims, abstention = "missing_output", [], None
        elif review is None:
            status, claims, abstention = "review_missing", [], None
        else:
            declared_review_status = review["review_status"]
            case_binding = "matched" if "schema_version" in review else "absent"
            status = "review_" + declared_review_status if case_binding == "matched" else "review_unbound"
            abstention = review["abstention"]
            claims = [{"claim_id": claim["claim_id"], "verdict": _verdict(claim["evidence"]),
                       "answer_span": claim["answer_span"], "evidence": claim["evidence"]}
                      for claim in review["claims"]]
            if status == "review_complete":
                counts["complete_no_claims"] += not claims
                counts["explicit_abstentions"] += abstention
                for claim in claims:
                    counts[claim["verdict"]] += 1
            elif status == "review_incomplete":
                counts["claims_excluded_incomplete"] += len(claims)
            else:
                counts["claims_excluded_unbound"] += len(claims)
        counts[status] += 1
        supported = sum(claim["verdict"] == "supported" for claim in claims)
        details.append({"id": case_id, "status": status, "case_binding": case_binding,
                        "declared_review_status": declared_review_status, "abstention": abstention, "claims": claims,
                        "supported_fraction": supported / len(claims) if status == "review_complete" and claims else None})
    total = len(cases)
    present = len(outputs)
    scored = sum(counts[key] for key in ("supported", "contradicted", "conflicting", "insufficient"))
    return {"schema_version": 2, "evaluation_type": "grounding", "cases_sha256": cases_sha256,
            "outputs_sha256": outputs_sha256, "reviews_sha256": reviews_sha256,
            "total_cases": total, "outputs_present": present, **counts,
            "output_coverage": present / total, "review_coverage": counts["review_complete"] / present if present else None,
            "total_claims_scored": scored, "supported_fraction": counts["supported"] / scored if scored else None,
            "cases": details}


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise InputError("Argumentos inválidos; usa --cases, --outputs y --reviews.")


def main(argv=None):
    parser = _Parser(prog="evals_lab.grounding", description="Agrega revisiones de sustento registradas.",
                     allow_abbrev=False)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--outputs", required=True)
    parser.add_argument("--reviews", required=True)
    parser.add_argument("--allow-unbound-reviews", action="store_true",
                        help="inspecciona revisiones antiguas sin incluirlas en métricas")
    try:
        args = parser.parse_args(argv)
        cases, outputs, reviews, cases_hash, outputs_hash, reviews_hash = load_inputs(
            args.cases, args.outputs, args.reviews, allow_unbound_reviews=args.allow_unbound_reviews)
        report = evaluate(cases, outputs, reviews, cases_sha256=cases_hash,
                          outputs_sha256=outputs_hash, reviews_sha256=reviews_hash)
        encoded = (json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                              allow_nan=False) + "\n").encode("ascii")
        if sys.stdout.buffer.write(encoded) != len(encoded):
            raise OSError("incomplete output")
        sys.stdout.buffer.flush()
        return 1 if (report["missing_output"] or report["review_missing"] or report["review_incomplete"] or
                     report["review_unbound"]) else 0
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
