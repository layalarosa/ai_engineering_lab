# Etiquetas de la línea base RAG

Este conjunto reutiliza los tres documentos didácticos de `01-rag/eval_data` y
las cuatro preguntas de `api_cases.json`. La revisión siguiente fue realizada
por el asistente a partir del contenido de esos tres documentos; **no existe una
revisión humana independiente**. Es un conjunto de aprendizaje, no una muestra
representativa ni una reserva independiente para comparar sistemas.

| Caso | Documentos relevantes | Razón del juicio sobre el corpus completo |
| --- | --- | --- |
| `chunking` | `fragmentacion` | Explica que el solapamiento conserva contexto entre fragmentos y ayuda con los límites. Los otros dos documentos no describen el solapamiento. |
| `embedding` | `embeddings` | Define embedding como representación numérica de texto. `persistencia` menciona embeddings, pero no explica qué representan; `fragmentacion` no los define. |
| `pgvector` | `persistencia` | Describe almacenamiento de embeddings y búsqueda por distancia con pgvector. Los otros dos documentos no describen capacidades de pgvector. |
| `sin_respuesta` | Ninguno | Ninguno de los tres documentos menciona la capital de Bolivia. El juicio se limita a este corpus; no implica que la pregunta no tenga respuesta fuera de él. |

La relevancia se refiere a información que permite responder a la pregunta, no
simplemente a compartir palabras. Un cambio de contenido o de pregunta obliga
a revisar todos los juicios afectados. `api_cases.json` conserva una respuesta
esperada para revisión manual; el adaptador solo verifica compatibilidad entre
su único documento esperado y estas listas.

Cada documento actual cabe en un único fragmento con la configuración de la
API (500 caracteres, solapamiento 50). Esta línea base no demuestra el efecto
de deduplicar varios fragmentos de un documento. El ejemplo sintético del
[diseño de integración](../../docs/rag-integration-design.md) sí ilustra esa
diferencia sin llamar a modelos.
