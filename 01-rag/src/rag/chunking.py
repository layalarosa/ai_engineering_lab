"""Fragmentación simple por caracteres, sin dependencias externas."""


def chunk_text(text: str, chunk_size: int, overlap: int = 0) -> list[str]:
    """Divide texto en fragmentos ordenados con solapamiento configurable."""
    if chunk_size <= 0:
        raise ValueError("chunk_size debe ser mayor que cero")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap debe estar entre cero y chunk_size - 1")
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return chunks
