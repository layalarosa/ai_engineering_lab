"""Ingesta y búsqueda efímera con embeddings de Ollama."""

from dataclasses import dataclass
from pathlib import Path

from .ingestion import DocumentChunk, ingest_text_file
from .ollama import embed
from .similarity import rank_vectors


@dataclass(frozen=True)
class SearchResult:
    chunk: DocumentChunk
    score: float


def search_file(path: str | Path, query: str, *, model: str, base_url: str = "http://localhost:11434", chunk_size: int = 500, overlap: int = 50, limit: int = 3) -> list[SearchResult]:
    if not query.strip():
        raise ValueError("la consulta no puede estar vacía")
    if limit <= 0:
        raise ValueError("limit debe ser mayor que cero")
    chunks = ingest_text_file(path, chunk_size, overlap)
    if not chunks:
        return []
    vectors = embed([chunk.text for chunk in chunks] + [query], model=model, base_url=base_url)
    return [SearchResult(chunks[index], score) for index, score in rank_vectors(vectors[-1], vectors[:-1], limit)]
