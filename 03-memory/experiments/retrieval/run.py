"""Comparación didáctica de recuperación sobre el corpus sintético local."""
import hashlib
import json
import math
import platform
import re
import statistics
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from memory_lab.memory import ConversationMemory

CORPUS = Path(__file__).with_name("cases.json")
CATEGORIES = {"reciente", "antiguo", "correccion", "variacion", "sin_respuesta", "limites"}
LABELS = {"relevante", "desactualizado", "otro"}
REPETITIONS = 100


def validate_case(case):
    """Valida datos sintéticos usando el contrato real para mensajes y límites."""
    for field in ("id", "family", "rationale"):
        if not isinstance(case.get(field), str) or not case[field].strip():
            raise ValueError(f"Campo inválido: {field}")
    if case.get("split") not in {"desarrollo", "reservado"} or case.get("category") not in CATEGORIES:
        raise ValueError("Partición o categoría inválida")
    query = case.get("query")
    if not isinstance(query, str) or not 1 <= len(query) <= 4000:
        raise ValueError("Consulta inválida")
    try:
        query.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Consulta no codificable en UTF-8") from None
    messages = case.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > 1000:
        raise ValueError("Historial inválido")
    memory = ConversationMemory()
    for message in messages:
        if not isinstance(message, dict) or message.get("label") not in LABELS:
            raise ValueError("Etiqueta de mensaje inválida")
        memory.append(message.get("role"), message.get("content"))
    # Validar parámetros sin confundir un último mensaje largo con límites inválidos.
    ConversationMemory().context(max_chars=case.get("max_chars"), max_messages=case.get("max_messages"))
    labels = {m["label"] for m in messages}
    if case["category"] == "sin_respuesta" and "relevante" in labels:
        raise ValueError("Un caso sin respuesta no debe tener relevantes")
    if case["category"] != "sin_respuesta" and "relevante" not in labels:
        raise ValueError("Falta contenido relevante")
    if case["category"] == "correccion" and "desactualizado" not in labels:
        raise ValueError("Falta la preferencia corregida")
    return memory


def load_cases():
    raw = CORPUS.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    if document.get("version") != 1 or not isinstance(document.get("cases"), list):
        raise ValueError("Versión o corpus inválido")
    cases = document["cases"]
    ids, families, distribution = set(), {}, Counter()
    for case in cases:
        validate_case(case)
        if case["id"] in ids:
            raise ValueError("Identificador duplicado")
        ids.add(case["id"])
        previous = families.setdefault(case["family"], case["split"])
        if previous != case["split"]:
            raise ValueError("Familia presente en ambas particiones")
        distribution[case["split"], case["category"]] += 1
    expected = {(split, category): count for split, count in (("desarrollo", 4), ("reservado", 2))
                for category in CATEGORIES}
    if dict(distribution) != expected:
        raise ValueError("Distribución de corpus inesperada")
    return cases, hashlib.sha256(raw).hexdigest()


def terms(text):
    return set(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold()))


def lexical(case):
    """Devuelve posiciones 1-based cronológicas, conservando duplicados."""
    query_terms = terms(case["query"])
    if not query_terms:
        return []
    ranked = []
    for position, message in enumerate(case["messages"], 1):
        overlap = len(query_terms & terms(message["content"]))
        if overlap:
            # El denominador es igual para todos los mensajes de una consulta.
            ranked.append((overlap, position))
    ranked.sort(reverse=True)
    chosen, used = [], 0
    for _, position in ranked:
        length = len(case["messages"][position - 1]["content"])
        if used + length <= case["max_chars"]:
            chosen.append(position)
            used += length
            if len(chosen) == case["max_messages"]:
                break
    return sorted(chosen)


def recency(case, memory):
    try:
        context = memory.context(max_chars=case["max_chars"], max_messages=case["max_messages"])
    except ValueError:
        # Los límites ya se validaron. No ocultar otros errores de programación.
        if len(case["messages"][-1]["content"]) > case["max_chars"]:
            return {"error": "ultimo_mensaje_supera_presupuesto", "positions": None}
        raise
    start = context["omitted_count"] + 1
    return {"error": None, "positions": list(range(start, start + len(context["messages"]))) }


def candidate(case):
    return {"error": None, "positions": lexical(case)}


def percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(percent * len(ordered)) - 1]


def run_case(case, repetitions=REPETITIONS):
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError("Las repeticiones deben ser positivas")
    memory = validate_case(case)
    output = {"id": case["id"], "split": case["split"], "category": case["category"],
              "relevantes": [i for i, msg in enumerate(case["messages"], 1) if msg["label"] == "relevante"],
              "desactualizados": [i for i, msg in enumerate(case["messages"], 1) if msg["label"] == "desactualizado"],
              "methods": {}}
    for name, select in (("recencia", lambda: recency(case, memory)), ("lexica", lambda: candidate(case))):
        samples, result = [], None
        for _ in range(repetitions):
            start = time.perf_counter_ns()
            current = select()
            samples.append(time.perf_counter_ns() - start)
            if result is not None and current != result:
                raise RuntimeError("Resultado no determinista")
            result = current
        positions = result["positions"]
        if positions is not None:
            if (positions != sorted(set(positions)) or len(positions) > case["max_messages"]
                    or any(not 1 <= p <= len(case["messages"]) for p in positions)):
                raise RuntimeError("Selección inválida")
            characters = sum(len(case["messages"][p - 1]["content"]) for p in positions)
            if characters > case["max_chars"]:
                raise RuntimeError("Selección excede el presupuesto")
            result["characters"] = characters
            result["message_count"] = len(positions)
        result["_samples_ns"] = samples
        output["methods"][name] = result
    return output


def fraction(numerator, denominator):
    return {"numerador": numerator, "denominador": denominator,
            "valor": numerator / denominator if denominator else None}


def aggregate(cases, results, method):
    relevant_recalls, precision = [], []
    success = complete = outdated = empty = absent = absent_nonempty = 0
    errors = Counter()
    message_counts, characters, samples = [], [], []
    for case, row in zip(cases, results):
        result = row["methods"][method]
        samples.extend(result["_samples_ns"])
        if result["error"]:
            errors[result["error"]] += 1
            continue
        success += 1
        selected = set(result["positions"])
        relevant = {i for i, msg in enumerate(case["messages"], 1) if msg["label"] == "relevante"}
        stale = {i for i, msg in enumerate(case["messages"], 1) if msg["label"] == "desactualizado"}
        if relevant:
            relevant_recalls.append(len(selected & relevant) / len(relevant))
            complete += relevant <= selected
        else:
            absent += 1
            absent_nonempty += bool(selected)
        if selected:
            precision.append(len(selected & relevant) / len(selected))
        else:
            empty += 1
        outdated += bool(selected & stale)
        message_counts.append(result["message_count"])
        characters.append(result["characters"])
    return {
        "casos": len(cases), "exitos": success, "errores": dict(errors),
        "cobertura_media": {"valor": statistics.mean(relevant_recalls) if relevant_recalls else None,
                            "denominador": len(relevant_recalls)},
        "precision_media": {"valor": statistics.mean(precision) if precision else None,
                            "denominador": len(precision), "salidas_vacias_excluidas": empty},
        "casos_completos": fraction(complete, len(relevant_recalls)),
        "con_desactualizados": fraction(outdated, success),
        "sin_respuesta_con_salida": fraction(absent_nonempty, absent),
        "sin_respuesta_vacios": absent - absent_nonempty,
        "mensajes_media": statistics.mean(message_counts) if message_counts else None,
        "caracteres_media": statistics.mean(characters) if characters else None,
        "tiempo_microsegundos": {"p50": percentile(samples, .50) / 1000 if samples else None,
                                 "p95": percentile(samples, .95) / 1000 if samples else None,
                                 "muestras": len(samples)},
    }


def paired(cases, results):
    differences, excluded = [], []
    for case, row in zip(cases, results):
        relevant = {i for i, msg in enumerate(case["messages"], 1) if msg["label"] == "relevante"}
        if not relevant:
            continue
        rec, lex = row["methods"]["recencia"], row["methods"]["lexica"]
        if rec["error"] or lex["error"]:
            excluded.append(case["id"])
            continue
        differences.append((len(set(lex["positions"]) & relevant) -
                            len(set(rec["positions"]) & relevant)) / len(relevant))
    return {"diferencia_media_lexica_menos_recencia": statistics.mean(differences) if differences else None,
            "casos_comparados": len(differences), "casos_excluidos_por_error": excluded}


def report(cases, digest, repetitions=REPETITIONS):
    rows = [run_case(case, repetitions) for case in cases]
    partitions = {}
    for split in ("desarrollo", "reservado"):
        indexes = [i for i, case in enumerate(cases) if case["split"] == split]
        selected_cases = [cases[i] for i in indexes]
        selected_rows = [rows[i] for i in indexes]
        methods = {method: aggregate(selected_cases, selected_rows, method) for method in ("recencia", "lexica")}
        categories = {}
        for category in sorted(CATEGORIES):
            ci = [i for i, case in enumerate(selected_cases) if case["category"] == category]
            cs, rs = [selected_cases[i] for i in ci], [selected_rows[i] for i in ci]
            categories[category] = {method: aggregate(cs, rs, method) for method in ("recencia", "lexica")}
        partitions[split] = {"metodos": methods, "categorias": categories,
                             "comparacion_emparejada": paired(selected_cases, selected_rows)}
    for row in rows:
        for result in row["methods"].values():
            del result["_samples_ns"]
    return {"version_corpus": 1, "sha256_corpus": digest,
            "sha256_ejecutor": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "fecha_utc": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
            "plataforma": platform.platform(), "repeticiones_por_caso_y_metodo": repetitions,
            "particiones": partitions, "casos": rows}


def main():
    cases, digest = load_cases()
    result = report(cases, digest)
    # Escapar Unicode mantiene la salida JSON portable en consolas Windows.
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
