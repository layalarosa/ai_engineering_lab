# API RAG real con proveedores simulados

El 17 de noviembre de 2026 a las 10:21 (America/La_Paz) se ejecutó el caso
sintético `mezcla_dato_orden` contra la API HTTP real de `01-rag` y una base
PostgreSQL/pgvector temporal. Los proveedores de embeddings y generación se
reemplazaron en el mismo proceso por funciones deterministas; no hubo llamadas
a Ollama ni a OpenAI.

La ejecución comprobó este recorrido:

1. `PUT /v1/documents/mezcla` devolvió HTTP 200 e indexó un fragmento.
2. Una consulta a la base confirmó el texto, origen, índice y modelo previstos.
3. `POST /v1/answers` devolvió `answered` y el capturador verificó que la fuente
   contenía el fragmento completo esperado.
4. El generador simulado comprobó que recibió la pregunta, la instrucción de
   sistema y el fragmento exactos; después devolvió una respuesta predeterminada.

El embedding ficticio devolvió siempre el vector bidimensional `[1.0, 0.25]`.
Se registraron dos llamadas simuladas de embeddings y una de generación. Las
llamadas de red a los clientes de Ollama y OpenAI estaban bloqueadas y no se
invocaron. La observabilidad de etapas de la API se deshabilitó en este ensayo;
no se probaron sus registros operativos.

Se confirmó el cierre de la API y la eliminación del contenedor de PostgreSQL.
Se comprobó la identidad de la imagen, `tmpfs` para datos y publicación del puerto
solo en `127.0.0.1`. El informe conserva hashes de entradas y código, resultados
y estado de limpieza; no contiene credenciales ni la respuesta capturada. El hash
de esa respuesta identifica sus bytes, pero no permite reconstruirlos o revisarlos.

**Límite:** la respuesta ya estaba fijada por el fixture. Esta ejecución prueba
el recorrido técnico para un caso, no la calidad de embeddings, el comportamiento
de un modelo ni la resistencia del RAG a instrucciones adversariales.

- [Informe de la ejecución](report.json).
- [Programa de integración](../../../tests/rag_api_postgres_fake_model_check.py).
- [Protocolo de seguridad](../../../docs/security-real-rag-protocol.md).
