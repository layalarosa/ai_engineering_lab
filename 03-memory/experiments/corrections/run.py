"""Ejecuta exclusivamente los casos de desarrollo del prototipo."""
import hashlib
import importlib.util
import json
import statistics
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prototype = load_module("corrections_prototype", HERE / "prototype.py")
baseline = load_module("retrieval_baseline", ROOT / "experiments" / "retrieval" / "run.py")
CATEGORIES = {"vigente", "cadenas", "claves", "ambiguo", "sin_respuesta", "metadatos",
              "intencion_presupuesto", "limites"}
LABELS = {"relevante", "desactualizado", "otro"}
ABSTENTIONS = {"sin_evidencia_registrada", "evidencia_ambigua", "intencion_no_soportada",
               "presupuesto_insuficiente", "sin_coincidencias", "sin_historial"}
ERRORS = {"entrada_invalida", "ultimo_mensaje_supera_presupuesto"}


def load_development():
    raw = (HERE / "development.json").read_bytes()
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("Corpus de desarrollo inválido")
    snapshots = document.get("snapshots")
    queries = document.get("queries")
    judgments = document.get("judgments")
    if not all(isinstance(items, list) for items in (snapshots, queries, judgments)):
        raise ValueError("Estructura de corpus inválida")
    snapshot_map = {item["id"]: item for item in snapshots}
    judgment_map = {item["query_id"]: item for item in judgments}
    if len(snapshot_map) != len(snapshots) or len(judgment_map) != len(judgments):
        raise ValueError("Identificadores duplicados")
    ids, categories = set(), Counter()
    for query in queries:
        qid = query["id"]
        if qid in ids or qid not in judgment_map or query["snapshot"] not in snapshot_map:
            raise ValueError("Consulta duplicada o referencia inexistente")
        ids.add(qid)
        if query["category"] not in CATEGORIES:
            raise ValueError("Categoría inválida")
        categories[query["category"]] += 1
        judgment = judgment_map[qid]
        snapshot = snapshot_map[query["snapshot"]]
        if (not isinstance(query["text"], str) or not 1 <= len(query["text"]) <= 4000
                or not isinstance(judgment["rationale"], str) or not judgment["rationale"].strip()):
            raise ValueError("Consulta o justificación inválida")
        query["text"].encode("utf-8")
        if (not isinstance(judgment["labels"], list) or len(judgment["labels"]) != len(snapshot["messages"])
                or any(label not in LABELS for label in judgment["labels"])
                or judgment["metadata_quality"] not in {"correctos", "incompletos", "falsos", "invalidos"}
                or type(judgment["resoluble"]) is not bool
                or not isinstance(judgment["expected_positions"], list)
                or any(type(p) is not int or not 1 <= p <= len(snapshot["messages"])
                       for p in judgment["expected_positions"])
                or (judgment["metadata_quality"] == "invalidos") != (judgment["expected_status"] == "entrada_invalida")
                or (judgment["resoluble"] and "relevante" not in judgment["labels"])):
            raise ValueError("Juicios inválidos")
        if not isinstance(snapshot["records"], list):
            raise ValueError("Registros inválidos")
        # El historial y los límites deben ser válidos incluso en casos con registros inválidos.
        memory = baseline.ConversationMemory()
        for message in snapshot["messages"]:
            if not isinstance(message, dict) or set(message) != {"role", "content"}:
                raise ValueError("Mensaje inválido")
            memory.append(message["role"], message["content"])
        baseline.ConversationMemory().context(max_chars=query["max_chars"], max_messages=query["max_messages"])
    if ids != set(judgment_map) or dict(categories) != {category: 3 for category in CATEGORIES}:
        raise ValueError("Distribución de desarrollo inesperada")
    return snapshot_map, queries, judgment_map, hashlib.sha256(raw).hexdigest()


def fraction(n, d):
    return {"numerador": n, "denominador": d, "valor": n / d if d else None}


def run_one(snapshot, query, judgment):
    # La candidata no recibe los juicios ni posiciones de respuesta esperada.
    messages = snapshot["messages"]
    selected = prototype.select(messages, snapshot["records"], query["intention"],
                                query["max_chars"], query["max_messages"])
    structured = {"estado": selected["estado"], "posiciones": selected["posiciones"]}
    legacy_case = {"messages": messages, "query": query["text"], "max_chars": query["max_chars"],
                   "max_messages": query["max_messages"]}
    memory = baseline.ConversationMemory()
    for message in messages:
        memory.append(message["role"], message["content"])
    recent = baseline.recency(legacy_case, memory)
    recency = {"estado": recent["error"] or ("seleccionado" if recent["positions"] else "sin_historial"),
               "posiciones": recent["positions"]}
    positions = baseline.lexical(legacy_case)
    lexical = {"estado": "seleccionado" if positions else "sin_coincidencias", "posiciones": positions}
    return {"id": query["id"], "category": query["category"],
            "relevantes": [i for i, label in enumerate(judgment["labels"], 1) if label == "relevante"],
            "desactualizados": [i for i, label in enumerate(judgment["labels"], 1) if label == "desactualizado"],
            "resoluble": judgment["resoluble"], "calidad_metadatos": judgment["metadata_quality"],
            "resultado_esperado": {"estado": judgment["expected_status"], "posiciones": judgment["expected_positions"]},
            "metodos": {"recencia": recency, "lexica": lexical, "estructurada": structured}}


def summarize(rows, method):
    valid = [r for r in rows if r["calidad_metadatos"] != "invalidos"]
    invalid = [r for r in rows if r["calidad_metadatos"] == "invalidos"]
    states = Counter(r["metodos"][method]["estado"] for r in rows)
    errors = [r for r in valid if r["metodos"][method]["estado"] in ERRORS]
    positive = [r for r in valid if r["relevantes"]]
    answerable = [r for r in valid if r["resoluble"]]
    absent = [r for r in valid if not r["relevantes"]]
    successes = [r for r in valid if r["metodos"][method]["estado"] not in ERRORS]

    def chosen(row):
        return set(row["metodos"][method]["posiciones"] or [])

    precision = [len(chosen(r) & set(r["relevantes"])) / len(chosen(r)) for r in successes if chosen(r)]
    recalls = [len(chosen(r) & set(r["relevantes"])) / len(r["relevantes"])
               for r in successes if r["relevantes"]]
    stale_by_quality = {}
    for quality in ("correctos", "incompletos", "falsos"):
        group = [r for r in valid if r["calidad_metadatos"] == quality]
        stale_by_quality[quality] = fraction(sum(bool(chosen(r) & set(r["desactualizados"])) for r in group),
                                             len(group))
    return {
        "estados": dict(states), "casos_validos": len(valid), "errores_validos": len(errors),
        "seleccion_util": fraction(sum(set(r["relevantes"]) <= chosen(r) and bool(r["relevantes"])
                                       and r["metodos"][method]["estado"] not in ERRORS for r in answerable),
                                    len(answerable)),
        "abstencion_con_evidencia": fraction(sum(r["metodos"][method]["estado"] in ABSTENTIONS
                                                 for r in positive), len(positive)),
        "abstencion_con_evidencia_por_estado": dict(Counter(r["metodos"][method]["estado"] for r in positive
                                                            if r["metodos"][method]["estado"] in ABSTENTIONS)),
        "seleccion_sin_evidencia": fraction(sum(bool(chosen(r)) for r in absent), len(absent)),
        "seleccion_desactualizada": fraction(sum(bool(chosen(r) & set(r["desactualizados"])) for r in valid),
                                               len(valid)),
        "seleccion_desactualizada_por_metadatos": stale_by_quality,
        "cobertura_media": {"valor": statistics.mean(recalls) if recalls else None, "denominador": len(recalls)},
        "precision_media": {"valor": statistics.mean(precision) if precision else None,
                            "denominador": len(precision),
                            "salidas_vacias_excluidas": sum(not chosen(r) for r in successes)},
        # Los métodos de referencia no reciben los metadatos y no pueden validarlos.
        "entradas_invalidas_rechazadas": (fraction(sum(r["metodos"][method]["estado"] == "entrada_invalida"
                                                        for r in invalid), len(invalid))
                                         if method == "estructurada" else None),
    }


def paired(rows, left, right):
    differences, excluded = [], []
    for row in rows:
        if row["calidad_metadatos"] == "invalidos" or not row["relevantes"]:
            continue
        l, r = row["metodos"][left], row["metodos"][right]
        if l["estado"] in ERRORS or r["estado"] in ERRORS:
            excluded.append(row["id"])
            continue
        relevant = set(row["relevantes"])
        differences.append((len(set(r["posiciones"]) & relevant) - len(set(l["posiciones"]) & relevant)) /
                           len(relevant))
    return {"comparados": len(differences), "excluidos_error": excluded,
            "diferencia_cobertura": statistics.mean(differences) if differences else None}


def build_report():
    snapshots, queries, judgments, corpus_hash = load_development()
    rows = [run_one(snapshots[q["snapshot"]], q, judgments[q["id"]]) for q in queries]
    methods = ("recencia", "lexica", "estructurada")
    categories = {category: {method: summarize([r for r in rows if r["category"] == category], method)
                             for method in methods} for category in sorted(CATEGORIES)}
    matching = sum(row["metodos"]["estructurada"] == row["resultado_esperado"] for row in rows)
    return {
        "particion": "desarrollo", "casos": len(rows),
        "sha256_corpus": corpus_hash,
        "sha256_prototipo": hashlib.sha256((HERE / "prototype.py").read_bytes()).hexdigest(),
        "sha256_ejecutor": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "resultados_esperados": {"coinciden": matching, "total": len(rows)},
        "coste_metadatos": {"instantaneas": len(snapshots),
                            "registros_en_instantaneas": sum(len(s["records"]) for s in snapshots.values()),
                            "tiempo_creacion": "no medido; registros sintéticos preparados manualmente",
                            "nota": "La candidata recibe intención y registros adicionales; no se mide latencia de producción."},
        "metodos": {method: summarize(rows, method) for method in methods},
        "categorias": categories,
        "comparacion_emparejada": {method: paired(rows, method, "estructurada") for method in ("recencia", "lexica")},
        "detalle": rows,
    }


def main():
    result = build_report()
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if result["resultados_esperados"]["coinciden"] != result["casos"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
