# 01 — RAG

RAG (Retrieval-Augmented Generation) recupera fragmentos relevantes de una fuente de información y los incorpora al contexto de un modelo para responder preguntas. Separa el conocimiento documental del modelo, pero la calidad de la respuesta depende de la ingesta, la recuperación y el contexto seleccionado.

## Primera etapa: fragmentación

Comenzamos con una función pura que divide texto en fragmentos de caracteres con solapamiento. El solapamiento conserva parte del contexto en los límites. Es una aproximación deliberadamente simple: los caracteres no equivalen a tokens y pueden cortar frases. Más adelante evaluaremos alternativas con documentos reales.

No se necesitan dependencias externas. Desde este directorio, con Python 3.10 o superior:

```powershell
python -m unittest discover -s tests -v
```

La función recibe texto, tamaño máximo y solapamiento; devuelve una lista ordenada de fragmentos. Rechaza tamaños inválidos y devuelve una lista vacía para texto vacío.

## Arquitectura prevista

Una canalización sencilla separará ingesta, fragmentación, embeddings, almacenamiento/búsqueda y generación. Inicialmente se implementarán funciones pequeñas y contratos claros; se introducirán adaptadores para PostgreSQL/pgvector y proveedores de modelos cuando se necesiten. Cada etapa podrá probarse por separado. Ollama será la opción local y OpenAI una alternativa configurable. No se incorporan servicios ni frameworks antes de tener una necesidad verificable.

## Próxima etapa propuesta

Leer documentos de texto desde disco y construir una ingesta verificable, antes de incorporar embeddings y búsqueda vectorial.

## Segunda etapa: ingesta de texto

`ingest_text_file` lee un archivo UTF-8 y aplica la fragmentación. Cada resultado contiene la ruta de origen, un índice desde cero y el texto; estos metadatos permiten rastrear y ordenar los fragmentos. La ruta identifica el archivo tal como se proporcionó, no es un identificador global estable. Un documento vacío no produce fragmentos.

```python
from rag.ingestion import ingest_text_file

chunks = ingest_text_file("documento.txt", chunk_size=500, overlap=50)
for chunk in chunks:
    print(chunk.source, chunk.index, chunk.text)
```

Para ejecutar este ejemplo, añade `src` a `PYTHONPATH`. Los errores de archivo inexistente, permisos y UTF-8 inválido se propagan al llamador; no se descartan documentos silenciosamente. La lectura carga el archivo completo en memoria y está pensada para documentos pequeños en esta etapa.

La siguiente etapa propuesta es estudiar embeddings y similitud mediante un ejemplo pequeño y comprobable, antes de conectar un proveedor de modelos y PostgreSQL.
