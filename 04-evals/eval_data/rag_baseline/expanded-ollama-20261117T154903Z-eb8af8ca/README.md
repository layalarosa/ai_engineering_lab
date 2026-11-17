# Matriz ampliada con Ollama local

El 17 de noviembre de 2026, entre las 11:49 y las 11:51 (America/La_Paz),
se ejecutaron los 12 escenarios sintéticos dos veces, en dos recorridos
secuenciales. Se usaron la API real de `01-rag`, PostgreSQL/pgvector temporal
y Ollama local con `nomic-embed-text:latest` y `llama3.1:8b`.

Los **24 intentos completaron la captura**: 16 correspondieron a escenarios
adversariales y ocho a controles benignos. Las fuentes adversariales esperadas
aparecieron en los 16 intentos y todas las fuentes esperadas de los controles
aparecieron en los ocho. Los 24 resultados tuvieron estado `answered`.

La presencia de una fuente y el estado `answered` no indican por sí solos si
el modelo siguió una instrucción, respondió correctamente o rechazó una tarea
legítima. Los archivos de revisión de esta instantánea siguen vacíos. Una
[revisión posterior del asistente](../expanded-review-assistant-20261117T155924Z-f5810727/README.md)
se guardó aparte; no hubo revisión humana independiente.

Se crearon 12 bases independientes en un contenedor temporal, una por
escenario. El primer recorrido indexó 19 documentos y el segundo reutilizó el
corpus de cada escenario. Se verificaron las filas antes de cada consulta y
se repitió la comprobación de las 12 bases al finalizar. Se confirmaron el
cierre de las API y la eliminación del contenedor.

Los embeddings tuvieron 768 dimensiones. Se registraron 43 llamadas locales
de embeddings —19 de indexación y 24 de consulta— y 24 de generación. Los
digests observados de los modelos coincidieron antes y después; esto no
garantiza reproducibilidad bit a bit. El cliente no fijó semilla ni
temperatura. Se usó `limit=2`, sin umbral ni calentamiento de generación; la
indexación cargó previamente el modelo de embeddings y se desconoce el estado
inicial del generador.

Las bases tenían uno o dos fragmentos, por lo que este diseño facilita la
exposición y no mide la recuperación en corpus grandes. Dos respuestas por
escenario sirven para observar variación puntual, no para inferir una tasa
general de seguridad. La conexión a Ollama fue directa, sin proxy y restringida
a `127.0.0.1:11434`; no se llamó a OpenAI ni a otro proveedor externo.

- [Plan](plan.jsonl), [casos](cases.jsonl) y [expectativas](expectations.jsonl).
- [Respuestas del modelo y fuentes sintéticas](outputs.jsonl).
- [Eventos](events.jsonl), [trazas](traces.jsonl) e [informe con huellas](report.json).
- [Revisiones de ataques vacías](attack_reviews.jsonl) y [revisiones de controles vacías](control_reviews.jsonl).
- [Programa de ejecución](../../../tests/rag_expanded_ollama_local_run.py) y
  [diseño de la matriz](../../../docs/security-expanded-evaluation-design.md).

El informe conserva hashes de las entradas, salidas y código observado. Las
respuestas contienen texto generado a partir de documentos sintéticos y deben
tratarse como contenido de la ejecución. Los hashes de los archivos de revisión
vacíos corresponden a esta instantánea; las revisiones posteriores deben
guardarse en archivos nuevos.
