# Diseño: conectar resultados de RAG con la evaluación de recuperación

**Estado: adaptador offline implementado.** Convierte un informe ya registrado
de `01-rag` a las listas de documentos esperadas por `04-evals`. La conversión
es local: no envía preguntas a la API, no genera respuestas ni accede a
PostgreSQL, Ollama o OpenAI.

## Qué resultado se está evaluando

La API `POST /v1/answers` de RAG busca hasta `limit` **fragmentos**, aplica el
umbral de similitud configurado y devuelve en `sources` los fragmentos usados
como contexto. El orden es el de la lista recibida. Con fuentes devuelve
`answered`; sin ellas, `no_context`. Una respuesta de error HTTP no es una lista
vacía. Cuando hay fuentes, el modelo puede responder que no dispone de
información suficiente: `answered` no garantiza que haya contestado ni que la
respuesta esté sustentada.

Por tanto, esta integración mediría la recuperación **expuesta como contexto
tras el umbral**, limitada por el número de fragmentos solicitado. No mide el
ranking completo anterior al filtro, el conjunto de todos los documentos
disponibles ni la calidad de la respuesta generada. El informe de
`01-rag/src/rag/evaluate_api.py` contiene `limit` y registros con `id`,
`question`, `expected_document_id`, `status`, `answer` y `sources`; también
contiene métricas y otros campos que no se usarán como entrada del cálculo de
`04-evals`. El informe puede contener preguntas, respuestas y fragmentos del
corpus y debe tratarse como dato potencialmente sensible.

## Entradas propuestas

1. **Informe JSON de RAG** producido por el evaluador de la API y guardado por
   el operador. Es un solo objeto JSON, no JSONL ni una respuesta HTTP aislada.
   La ejecución real y el almacenamiento del informe son pasos externos; el
   adaptador no invocará la API. El evaluador actual de RAG interrumpe la
   ejecución cuando una petición o respuesta es inválida, por lo que no
   garantiza producir un informe parcial cuando hay errores.
2. **Corpus JSONL de `04-evals`**, preparado independientemente, con el
   inventario completo de IDs que se pretenden evaluar. Debe corresponder al
   corpus y al modelo de embeddings usados en la ejecución; no se deduce de
   las fuentes devueltas ni de los documentos esperados. El contrato actual
   admite entre 1 y 1.000 documentos únicos.
3. **Casos JSONL de `04-evals`**, preparados y revisados independientemente,
   con las preguntas y las listas exhaustivas de documentos relevantes para
   ese inventario. El contrato actual admite entre 1 y 1.000 casos. Una lista
   vacía requiere una revisión que confirme que ningún documento del corpus
   es relevante; un caso no revisado se excluye del conjunto.

Los casos RAG actuales solo declaran un `expected_document_id` o `null` y una
respuesta esperada. Eso **no demuestra que la lista de relevantes sea
exhaustiva**, ni que `null` sea un juicio suficientemente revisado. Por eso el
adaptador no convertirá automáticamente esos valores en etiquetas completas.
Se necesita preparar los casos de `04-evals` de forma explícita. Para detectar
mezclas accidentales, el adaptador exigirá que la pregunta coincida exactamente
con `input` y que el documento esperado de RAG, cuando no sea `null`, figure
entre los relevantes de `04-evals`; si es `null`, exigirá lista relevante vacía.
Esta comprobación detecta contradicciones, pero no valida la corrección ni la
exhaustividad de las etiquetas. Se rechaza la entrada si hay discrepancias; no
se normalizan preguntas, mayúsculas ni IDs.

Los identificadores de caso de RAG solo se exigen como cadenas no vacías y
únicas en su evaluador; `04-evals` además exige
`[a-z0-9][a-z0-9_-]{0,63}`. Por tanto, no todos los informes de RAG serán
compatibles: se rechazarán IDs fuera del contrato, sin reescritura automática.
Los IDs de documento usan `[A-Za-z0-9_-]{1,64}` y distinguen mayúsculas.

## Conversión propuesta

Para cada registro del informe RAG:

1. Comprobar su ID, que exista en los casos `04-evals`, la pregunta y el juicio
   compatible descritos arriba. Rechazar casos duplicados o inesperados.
2. Validar `status` y `sources`: `answered` requiere una lista no vacía;
   `no_context`, una lista vacía. Cada fuente debe ser un objeto con
   `document_id` válido y presente en el corpus. La lista completa, incluso
   más allá de `k`, debe contener como máximo `limit` elementos. `limit` debe
   ser entero entre 1 y 20. El informe debe tener una respuesta `answer` no
   vacía por registro, como exige el evaluador de RAG; no se interpreta su
   contenido. Se ignoran `label`, `chunk_index`, `score`, `text` y las métricas
   agregadas del informe para este cálculo.
3. Recorrer todos los fragmentos de `sources` en su orden original, conservar
   la **primera aparición** de cada `document_id` y descartar repeticiones
   posteriores. No ordenar por ID ni recalcular puntuaciones. La lista obtenida
   tiene como máximo 20 documentos.
4. Emitir una línea JSONL con `id` y `retrieved_document_ids`. Un registro
   `no_context` válido produce `[]`. La ausencia de un registro de caso no
   produce ninguna línea: el evaluador de `04-evals` lo mostrará como
   `missing_response`, no como recuperación vacía. Los casos de salida siguen
   el orden del archivo de casos de `04-evals`, no el orden del informe RAG.

El evaluador `04-evals` aplica su `k` **después** de deduplicar. Para evitar
presentar como suficiente un límite obviamente menor al solicitado, la
conversión propuesta aceptará un `k` de 1 a 20 y rechazará `k > limit`. Esto es
necesario pero no garantiza disponer de `k` documentos distintos: los
fragmentos repetidos o el umbral pueden reducir la lista. Una lista más corta
no autoriza a suponer cómo se ordenarían documentos no observados.

### Ejemplo de posiciones

Supongamos `limit = 4`, `k = 2`, un único documento relevante `B` y las
fuentes `[A, A, B, C]`. El resultado de conversión es `[A, B, C]`:

| Cálculo | Resultado para `B` |
| --- | --- |
| Entre los dos primeros **fragmentos**, calculado manualmente | No aparece: hit 0, recíproco 0. |
| En los cuatro fragmentos usados por el informe RAG (`limit = 4`) | Posición 3: hit 1, recíproco 1/3. |
| Entre los dos primeros **documentos únicos** en `04-evals` | Posición 2: hit 1, recíproco 1/2. |

La primera fila ilustra un corte manual: el evaluador actual de RAG calcula
para el `limit` de la ejecución, no ofrece un segundo parámetro `k`. La
deduplicación permite que un documento observado después de `k` fragmentos
entre en los primeros `k` documentos. Las métricas anteriores responden a
preguntas distintas y no deben presentarse como una mejora directa.

## Contrato operativo y errores

Se propone un comando independiente de `04-evals` que lea las tres entradas y
escriba únicamente el JSONL convertido en stdout; el operador decide dónde
guardarlo y después lo proporciona al evaluador de recuperación existente.
No se modifica el informe RAG ni se copian preguntas, respuestas, texto de
fragmentos, puntuaciones o rutas a la salida. Los IDs siguen siendo datos
potencialmente sensibles.

Antes de escribir la primera línea, el adaptador debe validar el informe y las
tres entradas completas y construir el resultado acotado en memoria. La
validación debe rechazar archivo ilegible, JSON malformado, claves duplicadas,
valores numéricos no finitos o de más de 128 caracteres, IDs desconocidos o repetidos, estados y tipos
inválidos y documentos fuera del inventario. No se aceptan BOM ni UTF-8
inválido. Se propone un máximo de 2 MiB para el informe RAG; los archivos
JSONL mantienen los límites ya implementados por `04-evals`. Estos límites
pueden excluir informes grandes aunque RAG los haya generado correctamente.
La salida será determinista para los mismos bytes y `k`, en UTF-8 con escapes
ASCII y saltos LF. El `k` comprobado durante la conversión no se incorpora al
JSONL: al evaluar, el operador debe usar el mismo valor o uno menor, y nunca un
valor superior al `limit` del informe original.

Un error de entrada o de lectura devolverá código `2`, sin datos en stdout y
con mensaje sanitizado en stderr, sin rutas ni contenido. Una interrupción de
stdout puede dejar una salida parcial; en ese caso también se devuelve `2` y
el operador debe descartar el archivo. La conversión válida devolverá `0`
incluso si algunos casos no tienen respuesta o posteriormente fallan la
evaluación. El evaluador existente seguirá devolviendo `0`, `1` o `2` según su
propio contrato; no se confundirán sus códigos con los de conversión.

Para reproducir y comparar una ejecución, el operador debe conservar por
separado, sin secretos: hashes de los bytes del informe RAG, corpus, casos y
JSONL convertido; versión del código; `limit`, `k`, umbral y configuración de
modelos conocidos; y la identidad o versión del corpus realmente usado. El
informe de `04-evals` ya contiene hashes de corpus, casos y resultados
convertidos. Esos hashes identifican archivos, pero no demuestran que el
inventario coincida con la base de datos ni que las etiquetas sean correctas.
El informe RAG actual no registra toda esa configuración; si se desconoce, se
debe indicar y evitar una comparación que dependa de ella. Un hash tampoco
anonimiza datos predecibles. Este primer adaptador no crearía automáticamente
un manifiesto ni modificaría los formatos existentes para registrar metadatos.

## Verificación

Las pruebas usan datos sintéticos para comprobar, entre otros casos:

- Primera aparición, orden estable, repeticiones antes y después de `k`,
  límite de fragmentos, `k > limit` y listas con menos de `k` documentos únicos.
- `answered`, `no_context`, casos ausentes y registros inconsistentes; un
  error de transporte no se convierte en lista vacía.
- Preguntas o etiquetas contradictorias, IDs inválidos, corpus incompleto,
  documentos inesperados y casos desconocidos o duplicados.
- Rechazo de JSON inválido, BOM, claves repetidas, números no finitos y límites
  excedidos, incluso en campos que no se copian; errores sin texto sensible.
- Salida determinista, tubería hacia `04-evals`, código `0` de conversión y
  códigos independientes del evaluador, cierre de stdout y compatibilidad con
  las pruebas y la imagen Docker existentes.

No se utiliza la línea base histórica como si fuera un informe detallado: el
archivo `api_baseline.md` contiene un resumen, no las fuentes completas de
cada caso. Tampoco se pretende construir un cliente de red o probar un modelo
en esta etapa. Una ejecución real y el etiquetado revisado requerirán datos y
configuración adecuados.
