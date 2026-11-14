# Experimento de umbrales y revisión de respuestas

Experimento local sobre los tres documentos de ejemplo y diez preguntas de `calibration_cases.json`, con `nomic-embed-text`, búsqueda exacta en memoria, `limit=2` y un fragmento por documento. Se calcularon los embeddings una vez y se aplicaron los umbrales al mismo orden de resultados. No se generaron respuestas en este experimento.

| Umbral | Positivos con fuente esperada | Positivos sin contexto | Negativos sin contexto |
| --- | ---: | ---: | ---: |
| 0,4 | 6/6 | 0/6 | 0/4 |
| 0,5 | 6/6 | 0/6 | 1/4 |
| 0,6 | 6/6 | 0/6 | 1/4 |
| 0,7 | 3/6 | 3/6 | 1/4 |
| 0,8 | 1/6 | 5/6 | 3/4 |

Los negativos cercanos al tema muestran el límite de esta estrategia: la pregunta sobre una garantía de velocidad obtiene una similitud máxima de aproximadamente 0,802, aunque el documento no ofrece ninguna garantía. En cambio, la pregunta válida sobre similitud coseno tiene su fuente esperada en segunda posición, con una puntuación aproximada de 0,649. El umbral 0,7 elimina esta fuente válida y deja pasar la pregunta sin respuesta sobre rendimiento.

Estos datos no justifican adoptar un umbral. Son pocos casos, de un corpus artificial, y el conjunto usado para estudiar opciones no debe servir por sí solo para estimar su rendimiento futuro. El resultado en pgvector puede diferir ligeramente por precisión numérica; cambiar el modelo, los documentos, el tamaño de fragmento o `k` cambia la distribución de puntuaciones. Para tomar una decisión se necesita un conjunto separado de calibración y otro de evaluación, ejemplos reales y revisión de errores.

## Guía de revisión humana de respuestas

Para cada resultado de `rag.evaluate_api`, compara la respuesta con los fragmentos efectivamente enviados y con `expected_answer`:

1. **Recuperación:** ¿aparece la fuente esperada y contiene el dato necesario? Para casos sin respuesta, ¿los fragmentos carecen realmente del dato solicitado?
2. **Sustento:** ¿cada afirmación relevante se apoya en un fragmento? Marca como no sustentada cualquier afirmación adicional que el texto no respalde, aunque parezca plausible. Las etiquetas son pistas, no verificación automática.
3. **Cobertura:** ¿responde a los puntos esenciales de `expected_answer` sin omitirlos? Una fuente correcta no compensa una respuesta incompleta.
4. **Abstención:** cuando falta el dato, ¿la respuesta declara que no sabe y evita inventarlo? `no_context` es un resultado del filtro; `answered` también puede contener una abstención expresada por el modelo.

Registrar los juicios como **sí**, **no** o **no evaluable**, y anotar la afirmación y el fragmento que la respalda o contradice. Una coincidencia de palabras o una cita sintácticamente válida no demuestra sustento semántico. Este paso es manual: no hay un verificador automático de fidelidad en esta etapa.
