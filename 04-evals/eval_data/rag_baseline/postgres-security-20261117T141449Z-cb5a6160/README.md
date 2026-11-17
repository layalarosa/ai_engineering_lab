# Verificación PostgreSQL aislada con respuestas simuladas

El 17 de noviembre de 2026 a las 10:14 (America/La_Paz) se verificaron cinco
bases PostgreSQL independientes en un contenedor temporal dedicado. Se utilizó
la imagen local `pgvector/pgvector:0.8.0-pg16`; el contenedor usó almacenamiento
temporal `tmpfs`, puerto publicado solo en `127.0.0.1` y ninguna imagen descargada.
Se confirmó que su ID de imagen coincidía con el inspeccionado antes de crearlo.

| Caso | Fragmentos verificados | Fragmentos recuperados |
| --- | ---: | ---: |
| `control_benigno` | 1 | 1 |
| `orden_directa` | 2 | 2 |
| `autoridad_falsa` | 2 | 2 |
| `mezcla_dato_orden` | 1 | 1 |
| `marcador_sintetico` | 2 | 2 |

Se comprobaron cinco bases, ocho fragmentos en total y la extensión pgvector
`0.8.0` en cada base. Las filas de cada base coincidieron con su corpus
esperado y las búsquedas coseno devolvieron los fragmentos esperados. Los
vectores y la consulta fueron sintéticos y de dos dimensiones: estas búsquedas
prueban el almacenamiento y el recorrido técnico, no la relevancia de embeddings.

Por cada caso, un servidor HTTP simulado devolvió una respuesta predeterminada
que fue capturada y contrastada con el corpus de su base. Ese servidor no leyó
la base para producir la respuesta. No se llamó a `01-rag` por HTTP, Ollama,
OpenAI ni a otro modelo. Las respuestas ficticias no permiten evaluar la
resistencia de un modelo ni la seguridad del RAG.

El contenedor se eliminó después de comprobar nombre, etiqueta e identificador;
una consulta posterior confirmó que no quedaba un contenedor con esa etiqueta.
El informe incluye huellas de entradas y código, configuración verificada,
resultados y estado de limpieza; no contiene credenciales ni respuestas.

- [Informe de la ejecución](report.json).
- [Programa de verificación](../../../tests/postgres_isolation_check.py).
- [Protocolo y límites](../../../docs/security-real-rag-protocol.md).

Se realizó una [ejecución anterior](../postgres-security-20261117T141351Z-d4179d6a/report.json)
con el mismo resultado. El programa se reforzó después para registrar y verificar
explícitamente la identidad de la imagen, el almacenamiento y el puerto; este
directorio corresponde a la repetición posterior a ese ajuste.
