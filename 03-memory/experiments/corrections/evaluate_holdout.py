"""Evalúa un corpus reservado con la candidata y los criterios congelados."""
import copy
import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location("frozen_corrections_runner", HERE / "run.py")
frozen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frozen)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_plan():
    plan = json.loads((HERE / "evaluation-plan.json").read_text(encoding="utf-8"))
    if (plan.get("version") != 1 or plan.get("evaluador_sha256") != digest(Path(__file__))
            or plan.get("pruebas_sha256") != digest(ROOT / "tests/test_corrections_holdout_evaluator.py")
            or plan.get("freeze_sha256") != digest(HERE / "freeze.json")):
        raise ValueError("Cambió el evaluador o el manifiesto de congelación")
    check = subprocess.run([sys.executable, "-S", str(HERE / "verify_freeze.py")],
                           capture_output=True, timeout=10)
    if check.returncode:
        raise ValueError("No coincide la versión congelada")


def load_corpus(path):
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Corpus inválido")
    if any(not isinstance(data.get(k), list) for k in ("snapshots", "queries", "judgments")):
        raise ValueError("Corpus inválido")
    snapshots = {s["id"]: s for s in data["snapshots"]}
    judgments = {j["query_id"]: j for j in data["judgments"]}
    queries = data["queries"]
    if len(snapshots) != len(data["snapshots"]) or len(judgments) != len(data["judgments"]):
        raise ValueError("Identificador duplicado")
    category_counts, ids, families = Counter(), set(), set()
    for q in queries:
        if (q["id"] in ids or q["snapshot"] not in snapshots or q["id"] not in judgments
                or q["category"] not in frozen.CATEGORIES or not isinstance(q.get("family"), str)
                or not q["family"].strip() or q["family"] in families):
            raise ValueError("Consulta inválida")
        ids.add(q["id"])
        families.add(q["family"])
        category_counts[q["category"]] += 1
        s, j = snapshots[q["snapshot"]], judgments[q["id"]]
        if (not isinstance(s["messages"], list) or not isinstance(s["records"], list)
                or not isinstance(q["text"], str) or not 1 <= len(q["text"]) <= 4000
                or not isinstance(j["labels"], list) or len(j["labels"]) != len(s["messages"])
                or any(label not in frozen.LABELS for label in j["labels"])
                or j["metadata_quality"] not in {"correctos", "incompletos", "falsos", "invalidos"}
                or type(j["resoluble"]) is not bool or not isinstance(j["rationale"], str)
                or not j["rationale"].strip() or not isinstance(j["expected_positions"], list)
                or any(type(p) is not int or not 1 <= p <= len(s["messages"]) for p in j["expected_positions"])
                or (j["metadata_quality"] == "invalidos") != (j["expected_status"] == "entrada_invalida")
                or (j["resoluble"] and "relevante" not in j["labels"])):
            raise ValueError("Caso o juicio inválido")
        q["text"].encode("utf-8")
        memory = frozen.baseline.ConversationMemory()
        for message in s["messages"]:
            if not isinstance(message, dict) or set(message) != {"role", "content"}:
                raise ValueError("Mensaje inválido")
            memory.append(message["role"], message["content"])
        frozen.baseline.ConversationMemory().context(max_chars=q["max_chars"], max_messages=q["max_messages"])
    if ids != set(judgments) or dict(category_counts) != {category: 3 for category in frozen.CATEGORIES}:
        raise ValueError("Distribución reservada inválida")
    return snapshots, queries, judgments, hashlib.sha256(raw).hexdigest()


def evaluate_criteria(rows, unchanged, within_limits):
    structured = lambda row: row["metodos"]["estructurada"]
    valid = [row for row in rows if row["calidad_metadatos"] != "invalidos"]
    eligible = [row for row in valid if row["resoluble"] and row["calidad_metadatos"] == "correctos"]

    def valid_evidence(row):
        result = structured(row)
        selected = set(result["posiciones"] or [])
        return (result["estado"] == "seleccionado" and set(row["relevantes"]) <= selected
                and not selected.intersection(row["desactualizados"]))

    policy_states = {"evidencia_ambigua", "intencion_no_soportada", "presupuesto_insuficiente", "entrada_invalida"}
    required = [row for row in rows if row["resultado_esperado"]["estado"] in policy_states]
    no_answer = [row for row in valid if not row["relevantes"]]
    tests = {
        "evidencia_vigente_con_metadatos_correctos": (eligible, valid_evidence),
        "estados_de_abstencion_y_errores": (required, lambda row: structured(row) == row["resultado_esperado"]),
        "sin_salida_en_casos_sin_respuesta": (no_answer, lambda row: structured(row)["estado"] not in frozen.ERRORS
                                               and not structured(row)["posiciones"]),
    }
    result = {}
    for name, (group, predicate) in tests.items():
        failures = [row["id"] for row in group if not predicate(row)]
        result[name] = {"cumple": bool(group) and not failures, "evaluados": len(group), "incumplen": failures}
    result["limites_y_ausencia_de_mutacion"] = {"cumple": unchanged and within_limits,
                                                "historial_sin_cambios": unchanged,
                                                "presupuesto_respetado": within_limits}
    return result


def evaluate(snapshots, queries, judgments, corpus_hash):
    before = copy.deepcopy((snapshots, queries, judgments))
    rows = [frozen.run_one(snapshots[q["snapshot"]], q, judgments[q["id"]]) for q in queries]
    unchanged = before == (snapshots, queries, judgments)
    within_limits = True
    for q, row in zip(queries, rows):
        selected = row["metodos"]["estructurada"]["posiciones"]
        if selected:
            messages = snapshots[q["snapshot"]]["messages"]
            if (len(selected) > q["max_messages"] or any(not 1 <= p <= len(messages) for p in selected)
                    or sum(len(messages[p - 1]["content"]) for p in selected) > q["max_chars"]):
                within_limits = False
    methods = ("recencia", "lexica", "estructurada")
    return {
        "particion": "reservada_nueva_no_ciega", "casos": len(rows), "sha256_corpus": corpus_hash,
        "metodos": {method: frozen.summarize(rows, method) for method in methods},
        "categorias": {category: {method: frozen.summarize([r for r in rows if r["category"] == category], method)
                                    for method in methods} for category in sorted(frozen.CATEGORIES)},
        "comparacion_emparejada": {method: frozen.paired(rows, method, "estructurada")
                                   for method in ("recencia", "lexica")},
        "criterios": evaluate_criteria(rows, unchanged, within_limits),
        "coste_metadatos": {"instantaneas": len(snapshots),
                            "registros": sum(len(s["records"]) for s in snapshots.values()),
                            "tiempo_creacion": "no medido; elaboración sintética manual"},
        "detalle": rows,
    }


def main():
    verify_plan()
    corpus_path = HERE / "heldout.json"
    snapshots, queries, judgments, corpus_hash = load_corpus(corpus_path)
    result = evaluate(snapshots, queries, judgments, corpus_hash)
    result.update({"fecha_utc": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
                   "plataforma": platform.platform(), "sha256_evaluador": digest(Path(__file__)),
                   "sha256_plan": digest(HERE / "evaluation-plan.json"),
                   "sha256_freeze": digest(HERE / "freeze.json")})
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
