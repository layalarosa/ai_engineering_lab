# Resultado de la evaluación RAG local

Ejecución del 16 de noviembre de 2026, 23:13–23:15 UTC, con los tres
documentos y cuatro preguntas sintéticos de esta línea base. Las etiquetas
fueron revisadas por el asistente, sin revisión humana independiente.

Se usó `nomic-embed-text:latest` para embeddings (ID local `0a109f422b47`),
`llama3.1:8b` para generación (ID local `46e0c10c039e`), PostgreSQL con
pgvector en un contenedor efímero, `limit=2`, `k=2` y ningún umbral. La base
empezó vacía; se verificó que los tres documentos tenían un fragmento cada
uno. El servicio de la API utilizó un puerto local asignado por el sistema y
el contenedor temporal se eliminó al finalizar. No se utilizó OpenAI.

| Caso | Recuperación | Revisión de la respuesta generada |
| --- | --- | --- |
| `chunking` | `fragmentacion` en posición 1. | Explicó que el solapamiento conserva contexto y ayuda en los límites; corresponde al documento. |
| `embedding` | `embeddings` en posición 1. | Describió una representación numérica generada por un modelo; corresponde al documento. |
| `pgvector` | `persistencia` en posición 1. | Mencionó búsquedas por distancia y reutilización de vectores, pero omitió que pgvector permite almacenarlos, parte de la respuesta esperada. |
| `sin_respuesta` | Devolvió `embeddings` y `fragmentacion`; ninguno es relevante. | Indicó que los fragmentos no contenían información suficiente para responder. |

Las cuatro respuestas tenían estado `answered` porque se recuperaron fuentes.
Ninguna incluyó una etiqueta explícita de cita; aquí no se ha evaluado
automáticamente la corrección o el sustento de las respuestas. La revisión de
la tabla es manual por el asistente y no sustituye una revisión independiente.

Entre los **tres positivos**, Hit@2 y MRR@2 fueron `1,0`, con cobertura `3/3`:
en los tres apareció la fuente esperada en primer lugar. En el **único
negativo**, la tasa de listas no vacías fue `1/1`; Evals lo marca `failed`
porque recuperó documentos pese a no tener evidencia relevante etiquetada.
Ese estado describe la recuperación según el criterio fijado. La respuesta
generada sí reconoció la falta de información en esta ejecución, por lo que el
fallo de recuperación no demuestra una alucinación ni una falta de abstención.

`01-rag` también calculó Hit@2 y MRR@2 de `1,0`. En este conjunto cada
documento ocupa un solo fragmento y se usa el mismo corte, por lo que las
posiciones observadas coinciden; esto no implica equivalencia general entre las
mediciones por fragmentos y por documentos.

Los artefactos son sintéticos y contienen preguntas, respuestas y fragmentos:

- [Informe RAG](rag-report.json), con respuestas, fuentes y métricas por fragmento.
- [Resultados convertidos](outputs.jsonl), con IDs de documentos ordenados.
- [Informe Evals](eval-report.json), con métricas y cobertura por documento.
- [Metadatos](metadata.json), con hashes, IDs de modelos e imagen y versión de
  Git. El árbol tenía cambios sin confirmar; el commit registrado no describe
  por sí solo todo el código usado, por eso se incluyen hashes de archivos.

Una única ejecución y cuatro preguntas no permiten extrapolar la precisión,
comparar modelos de manera robusta ni fijar un umbral. La duración total incluyó
preparación y generación y no constituye una medición de rendimiento controlada.
