# Verificación PostgreSQL aislada con respuestas simuladas

El 17 de noviembre de 2026 a las 10:16 (America/La_Paz) se verificaron cinco
bases PostgreSQL independientes en un contenedor temporal dedicado con la
imagen local `pgvector/pgvector:0.8.0-pg16`. Se comprobó la identidad de la
imagen, el uso de `tmpfs` y la publicación del puerto solo en `127.0.0.1`.

| Caso | Fragmentos verificados | Fragmentos recuperados |
| --- | ---: | ---: |
| `control_benigno` | 1 | 1 |
| `orden_directa` | 2 | 2 |
| `autoridad_falsa` | 2 | 2 |
| `mezcla_dato_orden` | 1 | 1 |
| `marcador_sintetico` | 2 | 2 |

Las filas de las cinco bases coincidieron con sus corpus previstos: ocho
fragmentos en total. La extensión pgvector fue `0.8.0` y las búsquedas coseno
devolvieron los fragmentos esperados. Los vectores y la consulta fueron
sintéticos y de dos dimensiones; la prueba no mide relevancia de embeddings.

En cada caso, una API simulada devolvió una respuesta predeterminada que el
capturador procesó y contrastó con el corpus esperado. Esa API no leyó la base
para producir la respuesta. No se llamó a la API real de `01-rag`, a Ollama ni
a otros modelos. Las respuestas ficticias no miden resistencia a ataques.

El contenedor se eliminó tras verificar su nombre, etiqueta e identificador;
se confirmó que no quedaba otro contenedor con esa etiqueta. El informe registra
las huellas de las entradas y código observado, la configuración y la limpieza;
no contiene credenciales ni respuestas.

- [Informe de la ejecución](report.json).
- [Programa de verificación](../../../tests/postgres_isolation_check.py).
- [Protocolo y límites](../../../docs/security-real-rag-protocol.md).

Esta fue la última de tres verificaciones de esta fase. Las anteriores se
conservaron: [primera ejecución](../postgres-security-20261117T141351Z-d4179d6a/report.json)
y [segunda ejecución](../postgres-security-20261117T141449Z-cb5a6160/README.md).
Las repeticiones comprobaron ajustes en la comprobación de imagen y en el
reporte de recursos cuya limpieza no pueda confirmarse.
