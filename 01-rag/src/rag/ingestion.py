"""Lectura de documentos de texto y creación de fragmentos identificables."""

from dataclasses import dataclass
from pathlib import Path

from .chunking import chunk_text

MAX_DOCUMENT_BYTES = 1_000_000
MAX_CHUNKS = 1000

@dataclass(frozen=True)
class DocumentChunk:
    """Un fragmento y su posición dentro del documento de origen."""

    source: str
    index: int
    text: str


def ingest_text_file(path: str | Path, chunk_size: int, overlap: int = 0) -> list[DocumentChunk]:
    """Lee UTF-8 y divide el contenido conservando su origen y orden.

    Los errores de lectura y codificación se propagan para que quien llame
    pueda distinguirlos y decidir cómo tratarlos.
    """
    source = Path(path)
    with source.open("rb") as file:
        data = file.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"el documento supera {MAX_DOCUMENT_BYTES} bytes")
    text = data.decode("utf-8")
    return ingest_text(text, str(source), chunk_size, overlap)


def ingest_text(text: str, source: str, chunk_size: int, overlap: int = 0) -> list[DocumentChunk]:
    """Fragmenta texto en memoria sin interpretar el origen como ruta."""
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"el documento supera {MAX_DOCUMENT_BYTES} bytes")
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("tamaño o solapamiento de fragmentos inválido")
    if len(text) > chunk_size and 1 + (len(text) - chunk_size + chunk_size - overlap - 1) // (chunk_size - overlap) > MAX_CHUNKS:
        raise ValueError(f"el documento produciría más de {MAX_CHUNKS} fragmentos")
    return [
        DocumentChunk(source=source, index=index, text=chunk)
        for index, chunk in enumerate(chunk_text(text, chunk_size, overlap))
    ]
