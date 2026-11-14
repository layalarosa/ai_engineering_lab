"""Persistencia sencilla y búsqueda exacta con PostgreSQL y pgvector."""

import json

from .ingestion import DocumentChunk
from .search import SearchResult
from .similarity import cosine_similarity


def initialize(conn):
    """Crea el esquema; requiere permiso para instalar la extensión vector."""
    with conn.transaction():
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("""CREATE TABLE IF NOT EXISTS rag_models (
            model text PRIMARY KEY, dimensions integer NOT NULL CHECK (dimensions > 0))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS rag_chunks (
            model text NOT NULL REFERENCES rag_models(model), source text NOT NULL,
            chunk_index integer NOT NULL, content text NOT NULL, embedding vector NOT NULL,
            PRIMARY KEY (model, source, chunk_index))""")


def replace_document(conn, model: str, source: str, chunks: list[DocumentChunk], vectors: list[list[float]]):
    """Reemplaza atómicamente los fragmentos de un documento y modelo."""
    if not model.strip() or not source or len(chunks) != len(vectors):
        raise ValueError("modelo, origen o número de vectores inválido")
    if any(c.source != source or c.index != i for i, c in enumerate(chunks)):
        raise ValueError("los fragmentos deben pertenecer al origen y estar ordenados")
    dimension = len(vectors[0]) if vectors else None
    for vector in vectors:
        if len(vector) != dimension:
            raise ValueError("dimensiones inconsistentes")
        cosine_similarity(vector, vector)
    with conn.transaction():
        # Serializa escrituras de un modelo, incluidas las primeras inserciones.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (model,))
        row = conn.execute("SELECT dimensions FROM rag_models WHERE model = %s", (model,)).fetchone()
        if row and dimension is not None and row[0] != dimension:
            raise ValueError("la dimensión no coincide con los datos existentes del modelo")
        if not row and dimension is not None:
            conn.execute("INSERT INTO rag_models (model, dimensions) VALUES (%s, %s)", (model, dimension))
        conn.execute("DELETE FROM rag_chunks WHERE model = %s AND source = %s", (model, source))
        for chunk, vector in zip(chunks, vectors):
            conn.execute("INSERT INTO rag_chunks (model, source, chunk_index, content, embedding) VALUES (%s, %s, %s, %s, %s::vector)",
                         (model, source, chunk.index, chunk.text, json.dumps(vector, allow_nan=False)))


def search(conn, model: str, vector: list[float], limit: int = 3, *, source_prefix: str | None = None) -> list[SearchResult]:
    if not model.strip() or limit <= 0:
        raise ValueError("modelo o límite inválido")
    cosine_similarity(vector, vector)
    row = conn.execute("SELECT dimensions FROM rag_models WHERE model = %s", (model,)).fetchone()
    if row is None:
        return []
    if row[0] != len(vector):
        raise ValueError("la dimensión no coincide con los datos existentes del modelo")
    encoded = json.dumps(vector, allow_nan=False)
    rows = conn.execute("""SELECT source, chunk_index, content, 1 - (embedding <=> %s::vector) AS score
        FROM rag_chunks WHERE model = %s AND (%s::text IS NULL OR left(source, length(%s::text)) = %s::text)
        ORDER BY embedding <=> %s::vector, source, chunk_index LIMIT %s""",
        (encoded, model, source_prefix, source_prefix, source_prefix, encoded, limit)).fetchall()
    return [SearchResult(DocumentChunk(source, index, content), score) for source, index, content, score in rows]
