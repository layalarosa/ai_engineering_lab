# Cinco casos con API RAG real y proveedores simulados

El 17 de noviembre de 2026 a las 10:27 (America/La_Paz) se ejecutaron los
cinco casos sintéticos en orden. Cada caso usó una base independiente y una
instancia temporal de la API real de `01-rag`; todas las bases estaban en un
contenedor PostgreSQL/pgvector dedicado y temporal.

| Caso | Documentos indexados y fuentes verificadas | Resultado de captura |
| --- | ---: | --- |
| `control_benigno` | 1 | `ok_answered` |
| `orden_directa` | 2 | `ok_answered` |
| `autoridad_falsa` | 2 | `ok_answered` |
| `mezcla_dato_orden` | 1 | `ok_answered` |
| `marcador_sintetico` | 2 | `ok_answered` |

La API recibió los documentos mediante `PUT` y las consultas mediante `POST`.
Antes de cada consulta se verificaron las filas y, al terminar los cinco casos,
se comprobó de nuevo el corpus completo de cada base. Las fuentes devueltas y
el contexto entregado al generador simulado coincidieron con lo previsto. Se
confirmó el cierre de las cinco API y la eliminación del contenedor; no quedaron
casos fallidos ni sin iniciar.

Los embeddings fueron idénticos y sintéticos (`[1.0, 0.25]`), y cada respuesta
la fijó un fixture. El límite de búsqueda fue dos, igual o superior al número
de fragmentos de cada base. Así se verifica el recorrido técnico con fuentes
controladas, pero no la relevancia de embeddings, la recuperación a escala ni la
resistencia del sistema a instrucciones adversariales. Los clientes de Ollama
y OpenAI tenían bloqueadas sus llamadas de red y no las invocaron. La emisión
de trazas de la API estuvo deshabilitada; esta ejecución no valida sus logs.

El informe incluye estados, conteos, limpieza y hashes de entradas, código y
líneas de salida. No guarda credenciales ni respuestas; un hash no permite
reconstruir o revisar el contenido. Los resultados no son juicios humanos ni
una evaluación de seguridad de un modelo real.

- [Informe de la ejecución](report.json).
- [Programa de integración](../../../tests/rag_api_five_cases_fake_model_check.py).
- [Protocolo de seguridad](../../../docs/security-real-rag-protocol.md).
