# Medición local de latencia RAG

Ejecución iniciada el **17 de noviembre de 2026 a las 00:30 UTC**, con cuatro
preguntas sintéticas secuenciales, una repetición, `limit=2`, sin calentamiento
del cliente y con timeout de socket de 180 segundos. Se usaron Ollama local
(`nomic-embed-text:latest` y `llama3.1:8b`) y una base PostgreSQL/pgvector
temporal. La base empezó vacía, recibió los tres documentos didácticos y se
eliminó el contenedor al terminar. No se configuró OpenAI.

| Caso | Resultado | Latencia observada por el cliente |
| --- | --- | ---: |
| `chunking` | `ok_answered`, HTTP 200 | 26,282 s |
| `embedding` | `ok_answered`, HTTP 200 | 10,125 s |
| `pgvector` | `ok_answered`, HTTP 200 | 13,859 s |
| `sin_respuesta` | `ok_answered`, HTTP 200 | 18,281 s |

Se iniciaron y completaron los cuatro intentos; hubo **0 fallos observados de
4 intentos** y ninguno quedó sin iniciar. Con el método de rango más cercano,
el p50 de las cuatro respuestas exitosas es **13,859 s**: es el segundo valor
ordenado, no el promedio de los dos centrales. El mínimo es `10,125 s` y el
máximo `26,282 s`. El p95 es `null` porque el contrato exige al menos 20
muestras para ese cálculo. Las duraciones se almacenan en nanosegundos; esa
unidad no implica que el reloj tenga precisión de un nanosegundo.

Las cuatro respuestas tuvieron `status: answered` y fuentes no vacías según
la validación básica del cliente. Esto incluye la pregunta sin evidencia
etiquetada: `answered` **no** demuestra que el modelo haya dado una respuesta
correcta ni que haya dejado de abstenerse. El cliente descartó los cuerpos,
por lo que esta medición no permite evaluar el texto generado en estos mismos
intentos. Los juicios de calidad de la ejecución anterior corresponden a
otras respuestas y no se trasladan automáticamente a esta.

## Condiciones y límites

- El cliente no hizo llamadas de calentamiento. La indexación previa sí
  utilizó el modelo de embeddings; se desconoce si el modelo de generación ya
  estaba cargado. No se puede clasificar el primer intento como arranque frío.
- La medición incluye conexión, espera, lectura y validación básica desde el
  cliente. El timeout configurado limita operaciones de socket, no garantiza
  un plazo total ni la cancelación del trabajo remoto.
- Se usó Python 3.10.11 en Windows AMD64; no se registró un perfil completo de
  hardware ni el estado de otras cargas. Cuatro muestras y un solo orden no
  permiten establecer capacidad, comparar modelos o prometer latencias futuras.
- Los nombres de modelo y el contexto son declarados por el operador; los
  metadatos conservan también los digests observados de los modelos, la imagen
  de PostgreSQL y los hashes del corpus y del código. El árbol de trabajo tenía
  cambios sin confirmar, por lo que el commit no describe por sí solo todos los
  bytes usados. Los hashes no garantizan reproducibilidad ni autentican datos.

## Artefactos

- [Informe del cliente](report.json): resultados, tiempos y denominadores; no
  contiene los cuerpos de respuesta ni las credenciales.
- [Casos sintéticos](cases.jsonl) y [contexto declarado](context.json).
- [Metadatos](metadata.json): hashes, configuración observada y confirmación
  de limpieza del contenedor. Los archivos se conservan con sus bytes exactos
  para poder comparar sus hashes.

El código de salida fue `0`, pues los cuatro intentos recibieron respuestas
que cumplieron la validación básica. Eso no es una puntuación de calidad ni
una garantía de que el comportamiento se mantenga en otra ejecución.
