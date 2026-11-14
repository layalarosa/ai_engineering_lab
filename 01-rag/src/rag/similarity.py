"""Similitud coseno y recuperación sobre vectores proporcionados por el llamador."""

import math
from collections.abc import Sequence


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Calcula similitud coseno; rechaza vectores inválidos o sin dirección."""
    if not a or len(a) != len(b):
        raise ValueError("los vectores deben tener igual dimensión no vacía")
    if not all(math.isfinite(x) for x in (*a, *b)):
        raise ValueError("los vectores deben contener valores finitos")
    norm_a = math.hypot(*a)
    norm_b = math.hypot(*b)
    if norm_a == 0 or norm_b == 0 or not math.isfinite(norm_a) or not math.isfinite(norm_b):
        raise ValueError("los vectores deben tener norma finita y distinta de cero")
    return math.fsum((x / norm_a) * (y / norm_b) for x, y in zip(a, b))


def rank_vectors(query: Sequence[float], vectors: Sequence[Sequence[float]], limit: int = 3) -> list[tuple[int, float]]:
    """Devuelve índices y puntuaciones descendentes; los empates conservan orden."""
    if limit <= 0:
        raise ValueError("limit debe ser mayor que cero")
    # Validar la consulta incluso si no hay candidatos.
    cosine_similarity(query, query)
    scores = [(index, cosine_similarity(query, vector)) for index, vector in enumerate(vectors)]
    return sorted(scores, key=lambda item: item[1], reverse=True)[:limit]
