"""Lectura de documentos de texto y creación de fragmentos identificables."""

from dataclasses import dataclass
from pathlib import Path

from .chunking import chunk_text


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
    text = source.read_text(encoding="utf-8")
    return [
        DocumentChunk(source=str(source), index=index, text=chunk)
        for index, chunk in enumerate(chunk_text(text, chunk_size, overlap))
    ]
