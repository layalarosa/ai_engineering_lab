# Comparación exploratoria de recuperación

Se evaluaron dos formas de ordenar los mismos tres documentos con `nomic-embed-text`: similitud coseno y una mezcla de **0,8 × coseno + 0,2 × coincidencia léxica**. La coincidencia léxica es la proporción de palabras únicas de la consulta presentes en el documento, tras pasar a minúsculas, quitar acentos y excluir palabras comunes predefinidas. El peso 0,2 se fijó antes de ejecutar esta comparación; no se ajustó con el conjunto reservado.

Se usaron diez preguntas de calibración ya disponibles y diez preguntas distintas reservadas para esta comparación. Cada conjunto tiene seis preguntas con fuente esperada y cuatro sin respuesta conocida. El corpus es el mismo para ambos; las preguntas se redactaron manualmente para estos tres documentos, por lo que la separación no representa independencia de corpus ni una muestra de producción.

| Conjunto | Método | Hit@1 | Hit@2 | MRR |
| --- | --- | ---: | ---: | ---: |
| Calibración | Coseno | 5/6 | 6/6 | 0,917 |
| Calibración | Mezcla | 5/6 | 6/6 | 0,917 |
| Reservado | Coseno | 5/6 | 6/6 | 0,917 |
| Reservado | Mezcla | 5/6 | 6/6 | 0,917 |

Hit@k mide si la fuente esperada aparece en las primeras k posiciones. MRR es la media del inverso de la posición de esa fuente, considerando las tres posiciones. Los cuatro casos sin respuesta de cada conjunto no entran en estas métricas. Sin un filtro, ambas estrategias devuelven documentos también para esas preguntas: este experimento no mide abstención ni exactitud de respuestas generadas.

En el conjunto reservado, la pregunta sobre dónde guardar embeddings recuperó el documento esperado en segunda posición con ambas estrategias. En calibración, la pregunta sobre la dirección de vectores también quedó en segunda posición con ambas. La mezcla cambió algunas posiciones inferiores, pero no mejoró ninguna de las métricas medidas. No se incorpora a la API con esta evidencia.

La coincidencia exacta de palabras puede beneficiar términos técnicos específicos, pero no reconoce sinónimos y puede favorecer documentos que comparten palabras sin contener la respuesta. Las escalas de la puntuación léxica y del coseno son diferentes; el peso elegido es una hipótesis, no un valor universal. Para evaluar una implementación real se necesitan más documentos y fragmentos, consultas representativas, datos de evaluación nuevos y medición de errores, latencia y coste. No ajustar la mezcla sobre el conjunto reservado ya observado y volver a presentar ese mismo resultado como validación independiente.
