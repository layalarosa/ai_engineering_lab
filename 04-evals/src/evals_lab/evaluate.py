"""Coincidencia literal y recuentos con denominadores explícitos."""
from .cases import InputError


def evaluate(cases, outputs, *, dataset_sha256, outputs_sha256):
    """Evalúa registros previamente validados y no incluye textos en el informe."""
    known_ids = {case["id"] for case in cases}
    if any(output["id"] not in known_ids for output in outputs):
        raise InputError("Hay respuestas con IDs que no existen en los casos.")
    by_id = {output["id"]: output["output"] for output in outputs}
    details = []
    passed = failed = errors = 0
    for case in cases:
        case_id = case["id"]
        if case_id not in by_id:
            status, error_code = "error", "missing_response"
            errors += 1
        elif by_id[case_id] == case["expected_output"]:
            status, error_code = "passed", None
            passed += 1
        else:
            status, error_code = "failed", None
            failed += 1
        details.append({"id": case_id, "status": status, "error_code": error_code})
    valid = passed + failed
    total = len(cases)
    return {"schema_version": 1, "dataset_sha256": dataset_sha256, "outputs_sha256": outputs_sha256,
            "total": total, "passed": passed, "failed": failed, "errors": errors,
            "valid_responses": valid, "exact_match_valid": passed / valid if valid else None,
            "pass_rate_all": passed / total, "cases": details}
