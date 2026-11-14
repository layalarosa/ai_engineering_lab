# Línea base didáctica de la API

Ejecución local con PostgreSQL/pgvector y Ollama, usando `nomic-embed-text`, `llama3.1:8b`, los tres documentos de `eval_data`, `limit=2` y sin umbral de similitud. La prueba se ejecutó contra una base temporal aislada. Los resultados de generación pueden variar entre ejecuciones.

- **Recuperación:** Hit@2 = 1,0 y MRR@2 = 1,0 en tres preguntas positivas; la fuente esperada apareció primera en los tres casos.
- **Solapamiento:** la respuesta explica que conserva contexto en los límites y cita la fuente.
- **Embedding:** la respuesta describe su representación numérica, aunque no cita explícitamente el fragmento.
- **pgvector:** recupera el documento correcto, pero la respuesta no menciona explícitamente el almacenamiento y añade una afirmación de «alta eficiencia» que los fragmentos no sustentan. Encontrar el documento correcto no garantiza una respuesta completa o fundamentada.
- **Pregunta fuera del corpus:** la API devolvió `answered` y dos fragmentos irrelevantes. El modelo indicó que no tenía información suficiente; por ello `negative_no_context=0` no significa que el modelo haya inventado una respuesta en este caso. El estado refleja la existencia de fragmentos, no una evaluación automática de abstención.

Este conjunto es demasiado pequeño para comparar sistemas o fijar un umbral. Añadir preguntas variadas, casos difíciles y criterios de revisión antes de extraer conclusiones. Revisar manualmente cada respuesta y su evidencia; no interpretar Hit@k o MRR como medidas de exactitud de generación.
