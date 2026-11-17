# Diseño: preparar fuentes RAG para revisar sustento

**Estado: adaptador offline implementado.** Convierte un informe registrado de
la API RAG en respuestas y fragmentos para el agregador `evals_lab.grounding`.
No consulta servicios, no extrae afirmaciones ni crea revisiones o juicios.

## Por qué hace falta otra conversión

El adaptador de recuperación existente conserva solo la primera aparición de
cada documento y descarta el texto. Esto sirve para Hit@k a nivel de documento,
pero no para comprobar qué fragmento sustenta una afirmación. El adaptador
conserva **todos los fragmentos recibidos, en su orden**, incluso
si pertenecen al mismo documento. Su texto y el de la respuesta permanecerán
iguales como secuencias de puntos de código; no se recortarán, normalizarán ni
volverán a fragmentar.

Las fuentes de la API RAG son los fragmentos que pasaron el umbral y se
entregaron como contexto. Evaluar el sustento frente a ellas no demuestra que
el modelo haya usado causalmente el contenido, que la fuente sea verdadera ni
que la respuesta cubra toda la pregunta. El agregador tampoco verifica
semánticamente las anotaciones humanas.

## Entradas y alcance

El comando propuesto leerá dos archivos locales:

1. **Casos de sustento JSONL**, preparados de antemano, con exactamente `id` e
   `input`, según [grounding-design.md](grounding-design.md). Constituyen la
   lista de preguntas que se espera evaluar. Entre 1 y 1.000 registros, IDs
   únicos y entradas válidas de hasta 4.000 puntos de código.
2. **Informe JSON de RAG** producido por `rag.evaluate_api`: un objeto con
   `limit` y `cases`, no una respuesta HTTP individual. Los registros incluyen
   `id`, `question`, `status`, `answer` y `sources`. El informe real también
   contiene respuestas esperadas, métricas, puntuaciones y otros campos que el
   adaptador no utilizará. El informe y el JSONL resultante pueden contener
   preguntas, respuestas y texto sensible: el operador debe protegerlos.

Los casos se proporcionan independientemente porque un informe puede omitir
una pregunta. Se verifica que cada registro RAG tenga un ID existente en los
casos y que su `question` coincida exactamente con `input`; no se normalizan
espacios, mayúsculas ni Unicode. Se rechazan IDs desconocidos o repetidos. Si
falta un caso en el informe, no se inventa una respuesta: el JSONL no tendrá
esa fila y el agregador lo mostrará como `missing_output`. Se admite incluso
un informe sin registros de caso, siempre que su estructura y límite sean
válidos. El evaluador RAG actual normalmente termina sin informe completo si
falla una consulta; el adaptador no infiere la razón de una ausencia.

Las etiquetas `expected_document_id` del informe no se convierten en juicios
de sustento y no se necesita un inventario vectorial. La corrección de la
pregunta, la exhaustividad de las fuentes y su procedencia siguen requiriendo
verificación externa.

## Transformación de cada caso

Para cada respuesta válida, el adaptador emitirá exactamente:

```json
{"id":"q1","answer":"...","sources":[{"source_id":"s1","document_id":"docA","chunk_index":0,"text":"..."}]}
```

`answer` se copia sin cambios de valor. Cada elemento de `sources` conserva
`document_id`, `chunk_index` y `text`. Se asigna `source_id` a partir de la
posición de la fuente **dentro de ese caso**: la primera es `s1`, la segunda
`s2`, y así sucesivamente. El informe debe contener las etiquetas de la API en
el mismo orden: `[1]` corresponde a `s1`, `[2]` a `s2`, etc. El adaptador
rechazará etiquetas ausentes o inconsistentes; no deducirá identidades a partir
del nombre del documento ni ordenará por puntuación.

Esta validación es deliberadamente más estricta que la del evaluador RAG
actual, que comprueba `document_id` pero no exige `label`, `chunk_index` ni
`text`. Por tanto, algunos informes aceptados por ese evaluador no serán
convertibles. Los informes de la API actual sí incluyen esos campos. No se
garantiza que los futuros formatos de API sigan siendo compatibles.

Una respuesta `answered` debe tener fuentes no vacías; una respuesta
`no_context` debe tener `sources: []`. En ambos casos se conserva la respuesta
de texto. El campo `status` no existe en el formato de salida de sustento y no
se traduce en una anotación de abstención ni en un juicio automático. El
informe RAG original debe conservarse si se necesita consultar ese estado. Una
respuesta HTTP de error no es una respuesta vacía y no se acepta como informe.

### Ejemplo con dos fragmentos del mismo documento

Caso de entrada:

```jsonl
{"id":"q1","input":"Que ocurre?"}
```

Informe RAG abreviado; los campos que aparecen son suficientes para el diseño:

```json
{"limit":2,"cases":[{"id":"q1","question":"Que ocurre?","status":"answered","answer":"Se preserva el contexto.","sources":[{"label":"[1]","document_id":"docA","chunk_index":0,"text":"Parte A.\n"},{"label":"[2]","document_id":"docA","chunk_index":1,"text":"Parte B."}]}]}
```

Salida JSONL determinista, con claves ordenadas y separadores compactos:

```jsonl
{"answer":"Se preserva el contexto.","id":"q1","sources":[{"chunk_index":0,"document_id":"docA","source_id":"s1","text":"Parte A.\n"},{"chunk_index":1,"document_id":"docA","source_id":"s2","text":"Parte B."}]}
```

Ambos fragmentos se mantienen: `[1] → s1` y `[2] → s2`. La secuencia `\n` en
JSON representa un salto de línea dentro del texto, no un separador de
registros. El SHA-256 de los bytes de esta línea de salida **sin su LF final**
es `ecefd59e30192c731cb6669492b250e13c4f46aa9ce3b98cb7d70d32d9020a03`.
Una revisión posterior deberá usar ese valor como `response_sha256`; el
ejemplo no contiene una revisión ni juzga si la respuesta está sustentada.

## Ejecución, validación y límites

El comando tiene la forma:

```text
python -m evals_lab.rag_grounding_adapter --report INFORME.json --cases CASOS.jsonl
```

El límite `limit` del informe debe ser un entero no
booleano entre 1 y 20; la lista `cases` puede contener hasta 1.000 elementos.
Cada lista de fuentes debe tener como máximo `limit` elementos. La respuesta
de RAG debe ser una cadena no vacía tras `strip()` y de hasta 4.000 puntos de
código. Cada fuente debe incluir `label`, un `document_id` válido,
`chunk_index` entero no booleano entre 0 y 999, y `text` de hasta 4.000 puntos
de código; el texto puede estar vacío, aunque no permitirá señalar un intervalo
no vacío. Cadenas relevantes deben poder codificarse en UTF-8 estricto. Los
IDs de caso siguen el contrato de sustento y distinguen mayúsculas.

Se mantienen los límites de 2 MiB para el informe RAG y cada archivo JSONL y
65.536 bytes por línea de salida, sin contar LF. Esos límites se comprueban
**después de serializar** con `ensure_ascii=True`, claves ordenadas y
separadores compactos: los escapes Unicode pueden ocupar más bytes que el
texto original. También se valida el tamaño total del JSONL generado antes de
publicarlo. No se truncan respuestas ni fuentes para que entren: un informe
RAG válido pero demasiado grande para sustento será rechazado. El lector del
informe rechazará JSON malformado, BOM, UTF-8 inválido, claves duplicadas,
números no finitos o demasiado largos y datos excesivos, incluidos campos que
se ignoran para la conversión. El lector JSONL conservará sus límites y
validaciones actuales.

El orden de salida seguirá los casos de sustento, independientemente del orden
del informe; faltantes simplemente no generan línea. Para las mismas entradas
en bytes, la salida será determinista, ASCII válido como UTF-8, con un LF por
registro. La representación JSON puede diferir en escapes de la del informe
RAG, pero los valores de `answer` y `text` decodificados deben ser idénticos;
no se afirma identidad de los bytes de ambos archivos.

El adaptador validará ambas entradas y toda la salida antes de escribir en
stdout. Emitirá `0` si la conversión es válida, incluso si faltan casos; `2`
si una entrada es inválida o no se puede completar la conversión; `--help`
terminará con `0`. Los mensajes de error irán a stderr sin rutas ni contenido.
Una interrupción de stdout puede dejar bytes parciales: se debe descartar el
archivo. El resultado intencionalmente **sí contiene respuestas y texto de
fuentes**; no debe tratarse como un informe sanitizado.

## Revisiones y trazabilidad

El adaptador no emite un archivo de revisiones. Tras la conversión, una persona
puede inspeccionar cada respuesta y sus fuentes y crear anotaciones según el
contrato de sustento. Un archivo de revisiones vacío es válido: el agregador
marcará las respuestas como `review_missing`, sin convertirlas en puntuaciones.
La abstención debe revisarse como contenido de la respuesta, no deducirse de
`status` ni de la presencia o ausencia de fragmentos.

El `response_sha256` de una revisión se calcula sobre la línea **de salida**
en bytes, sin LF o CRLF, no sobre la línea o el hash del informe RAG original.
Un cambio en respuesta, orden, ID de fuente o serialización invalida la
revisión. Si solo cambia un campo RAG descartado, como `score`, la línea
convertida puede ser igual aunque el informe original tenga otro hash: hay que
conservar por separado su SHA-256 y los metadatos de la ejecución.

Desde la versión 2, el agregador también exige `case_sha256` sobre la línea
exacta del caso y rechaza una revisión si cambia. Los registros anteriores sin
ese vínculo se rechazan por defecto; pueden inspeccionarse explícitamente sin
incluirlos en las métricas. El [contrato de vinculación](review-binding-design.md)
describe los bytes, los códigos de salida y la migración; los hashes no prueban
la corrección ni la autenticidad de los juicios.

## Verificación

Las pruebas utilizan datos sintéticos sin llamar a modelos y cubren:

- Dos fragmentos de un documento, identidad `[n] → sN`, orden de fuentes y
  casos, texto con saltos y Unicode, y respuesta `no_context`.
- Casos faltantes, informe vacío, respuestas y etiquetas incompatibles,
  fuentes sin campos, `chunk_index` booleano o fuera de rango y límites de
  archivo y de línea tras escapar Unicode.
- Rechazo de JSON inválido, claves duplicadas, valores no finitos, IDs
  duplicados o desconocidos, preguntas diferentes y errores sin contenido ni
  rutas; salida completa solo tras validar todos los registros.
- Hash de línea exacto y rechazo por el agregador de una revisión asociada a
  una versión anterior; prueba del flujo con un archivo de revisiones vacío
  que produzca `review_missing`, no una puntuación de sustento.
- Paridad local y Docker, preservación de los modos existentes y ausencia de
  llamadas de red o dependencias nuevas.

El informe registrado de la línea base se convirtió también sin llamar a
modelos: las cuatro respuestas y sus textos se conservaron, y con revisiones
vacías el agregador indicó cuatro revisiones pendientes sin puntuar el sustento.
La creación de anotaciones para esas respuestas y cualquier revisión o juez
automáticos requieren una fase posterior.
