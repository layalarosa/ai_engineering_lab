# Matriz ampliada con API RAG real y proveedores simulados

El 17 de noviembre de 2026 a las 11:42 (America/La_Paz) se ejecutaron los
12 escenarios sintéticos dos veces, en dos recorridos secuenciales. Los 24
intentos completaron el recorrido HTTP por la API real de `01-rag`; ninguno
falló ni quedó sin iniciar.

Se crearon 12 bases PostgreSQL/pgvector separadas en un contenedor temporal:
una por escenario, reutilizada para sus dos repeticiones. En el primer recorrido
se indexaron 19 documentos mediante la API; el segundo reutilizó los corpus.
Antes de cada consulta se verificaron las filas esperadas y, al terminar, se
comprobó de nuevo cada base para detectar contaminación entre escenarios.
Las fuentes devueltas y el contexto entregado al generador ficticio coincidieron
con los documentos previstos.

Los embeddings fueron vectores ficticios e idénticos de dos dimensiones, y las
respuestas provinieron de fixtures predeterminados. Los clientes de Ollama y
OpenAI tenían bloqueadas sus llamadas de red y no se invocaron. La captura
ejercita la API, PostgreSQL y pgvector, pero no mide relevancia de embeddings,
variación de un modelo ni resistencia a instrucciones. La emisión de trazas de
la API se deshabilitó durante este ensayo.

Se confirmó el cierre de las 24 API temporales y la eliminación del contenedor.
Se comprobó la imagen local, el uso de `tmpfs` y el puerto publicado en
loopback. Las revisiones permanecen vacías: los 16 ataques y ocho controles
están pendientes de revisión y no constituyen juicios sobre un modelo real.

- [Plan](plan.jsonl), [casos](cases.jsonl) y [expectativas](expectations.jsonl).
- [Respuestas ficticias](outputs.jsonl), [eventos](events.jsonl) e [informe](report.json).
- [Revisiones de ataques vacías](attack_reviews.jsonl) y [revisiones de controles vacías](control_reviews.jsonl).
- [Programa de integración](../../../tests/rag_expanded_postgres_fake_run.py) y
  [diseño de la matriz](../../../docs/security-expanded-evaluation-design.md).

El informe incluye hashes de las entradas, los artefactos y el código observado.
Las respuestas son sintéticas; no se guardaron credenciales. Los hashes de los
archivos de revisión vacíos describen esta instantánea, por lo que revisiones
posteriores deben guardarse en archivos nuevos.
