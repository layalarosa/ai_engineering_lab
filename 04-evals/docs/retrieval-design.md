# Diseño propuesto: evaluación de recuperación

**Estado: contrato implementado para evaluación offline.** Este documento define
una evaluación local de listas de documentos ya recuperados. No consulta un
modelo, una API, una base de datos ni otro proyecto del monorepo.

## Qué queremos medir

Para cada pregunta con documentos relevantes etiquetados, Hit@k indica si al
menos uno aparece entre los primeros `k` documentos recuperados. MRR@k también
tiene en cuenta la posición del primer documento relevante: su contribución es
`1 / posición`, o cero si ninguno aparece en las primeras `k` posiciones.
La posición comienza en uno. Esta última regla sigue la idea del rango
recíproco utilizada por [TREC](https://trec.nist.gov/data/qa.html).

Estas métricas no miden cuántos documentos relevantes se recuperaron en total,
ni si una respuesta generada es correcta, está sustentada o es segura. Para
varios documentos relevantes, solo cuenta la primera coincidencia: no son
Recall@k. Tampoco miden latencia, coste ni calidad general del modelo.

La unidad propuesta es el **documento único**, no el fragmento. El evaluador
existente de `01-rag` calcula el rango sobre una lista de fuentes que puede
contener varios fragmentos del mismo documento. Por eso sus posiciones y
puntuaciones no son directamente equivalentes a las de este diseño. Una
integración futura tendría que decidir y documentar cómo deduplicar y conservar
el orden; no se implementa aquí.

## Entradas y etiquetas

Se proponen tres archivos JSONL UTF-8, con un objeto por línea:

1. `corpus.jsonl`: inventario de identificadores de documentos disponibles.
   Cada registro tiene exactamente `document_id`.
2. `cases.jsonl`: preguntas y documentos relevantes. Cada registro tiene
   exactamente `id`, `input` y `relevant_document_ids`.
3. `outputs.jsonl`: resultados ordenados ya registrados. Cada registro tiene
   exactamente `id` y `retrieved_document_ids`.

El inventario permite detectar IDs ajenos al corpus en lugar de clasificarlos
silenciosamente como irrelevantes. Las etiquetas deben prepararse para ese
corpus fijo: una lista vacía expresa que **ningún documento del corpus fue
etiquetado como relevante para el caso**, y los documentos omitidos se tratan
como no relevantes. El evaluador no puede comprobar que las etiquetas sean
correctas o exhaustivas; los casos sin juicio suficiente deben excluirse hasta
etiquetarlos. Los juicios incompletos pueden sesgar estas mediciones
([NIST](https://www.nist.gov/publications/retrieval-evaluation-incomplete-information)).
Que no exista evidencia etiquetada en este corpus no demuestra que no exista
una respuesta en el mundo.

Los IDs de caso siguen el contrato del MVP: `[a-z0-9][a-z0-9_-]{0,63}`.
Los IDs de documento cumplen `[A-Za-z0-9_-]{1,64}` y distinguen mayúsculas.
`input` admite cadenas vacías o de hasta 4.000 puntos de código Unicode
codificables en UTF-8; documenta la pregunta, pero no se envía a ningún sistema.
El corpus y los casos contienen entre 1 y 1.000 registros cada uno; las
respuestas, entre 0 y 1.000. Los IDs son únicos en cada archivo y las
respuestas deben referirse a casos existentes.

Cada lista de documentos contiene entre 0 y 20 IDs únicos, todos presentes en
el corpus. El orden de `retrieved_document_ids` es el orden de recuperación; el
de los relevantes no afecta al resultado. Este límite inicial no representa
casos con más de 20 documentos relevantes o recuperados. Se valida toda la
lista, incluso después de `k`: IDs repetidos o desconocidos son errores de
entrada. Se rechazan también campos adicionales o ausentes, tipos inválidos,
IDs duplicados, objetos con claves repetidas, líneas vacías y JSON inválido.

Se conservan los límites de lectura del MVP actual: como máximo 2 MiB por
archivo y 65.536 bytes por línea sin contar LF o CRLF, UTF-8 estricto y sin BOM.
No se publicará ningún informe si una entrada es inválida o no puede leerse.
Los mensajes de error no incluirán contenido ni rutas. Como en el MVP, una
interrupción durante la escritura en stdout podría dejar una salida parcial.

## Cálculo y casos sin evidencia

`k` será un entero común para toda la ejecución, de 1 a 20, con valor por
defecto 3. Para cada respuesta válida se usan hasta los primeros `k` IDs; una
lista más corta es válida y no se rellena.

Para cada caso con al menos un documento relevante y respuesta registrada:

- `rank` es la posición de la primera coincidencia entre los primeros `k`, o
  `null` si no existe; `hit` es 1 cuando existe esa coincidencia y 0 en otro caso.
- `reciprocal_rank` es `1 / rank` cuando existe, y 0 en otro caso.
- `Hit@k = suma(hit) / positivos_evaluados`.
- `MRR@k = suma(reciprocal_rank) / positivos_evaluados`.

Si no hay positivos evaluados, ambas métricas son `null`. Un caso positivo con
lista recuperada vacía es una respuesta válida, con `hit = 0` y estado `failed`.

Para los casos sin documentos relevantes etiquetados se informan por separado
el número de respuestas vacías y no vacías entre los primeros `k`, y
`negative_nonempty_rate = respuestas_no_vacías / negativos_evaluados`; es
`null` si no hay negativos evaluados. Una lista vacía tiene estado `passed` y
una no vacía, `failed`. Este criterio describe el comportamiento del
**recuperador** con la etiqueta del caso: no demuestra que el modelo generativo
se haya abstenido ni que el documento devuelto contenga una respuesta. Al
comparar sistemas, hay que considerar que algunos siempre devuelven `k`
documentos y otros aplican filtros o umbrales.

Si falta la fila de respuesta para un caso, se registra `error` con código
`missing_response`. No equivale a una lista vacía y se excluye de los
denominadores de las métricas anteriores. El informe mostrará, por grupo de
positivos y negativos, el total, los evaluados y los ausentes, además de la
cobertura `evaluados / total` (`null` si el grupo está vacío). También mostrará
los conteos globales `passed`, `failed` y `errors`. No se combinarán las
puntuaciones de positivos y negativos en una única tasa de calidad.

## Ejemplo completo

Corpus sintético:

```jsonl
{"document_id":"DOC_A"}
{"document_id":"DOC_B"}
{"document_id":"DOC_C"}
{"document_id":"DOC_D"}
{"document_id":"DOC_E"}
{"document_id":"DOC_Q"}
{"document_id":"DOC_X"}
```

Casos sintéticos:

```jsonl
{"id":"q1","input":"Pregunta 1","relevant_document_ids":["DOC_A","DOC_B"]}
{"id":"q2","input":"Pregunta 2","relevant_document_ids":["DOC_C"]}
{"id":"q3","input":"Pregunta 3","relevant_document_ids":[]}
{"id":"q4","input":"Pregunta 4","relevant_document_ids":[]}
{"id":"q_missing","input":"Pregunta 5","relevant_document_ids":["DOC_E"]}
```

Respuestas registradas:

```jsonl
{"id":"q1","retrieved_document_ids":["DOC_X","DOC_B","DOC_A"]}
{"id":"q2","retrieved_document_ids":["DOC_A","DOC_D"]}
{"id":"q3","retrieved_document_ids":[]}
{"id":"q4","retrieved_document_ids":["DOC_Q"]}
```

Con `k = 3`:

| Caso | Grupo | Resultado | Estado |
| --- | --- | --- | --- |
| `q1` | Positivo | Primera coincidencia en posición 2: hit 1, recíproco 0,5. | `passed` |
| `q2` | Positivo | Ningún documento relevante: hit 0, recíproco 0. | `failed` |
| `q3` | Negativo | Lista vacía. | `passed` |
| `q4` | Negativo | Lista no vacía. | `failed` |
| `q_missing` | Positivo | Falta la respuesta. | `error` |

Hay tres positivos, de los cuales dos son evaluables: **Hit@3 = 1/2 = 0,5**
y **MRR@3 = (1/2 + 0)/2 = 0,25**. La cobertura positiva es `2/3`; la respuesta
ausente no se incluye en esas dos métricas. De dos negativos evaluables, uno
devuelve documentos: `negative_nonempty_rate = 1/2`. Hay dos casos `passed`, dos
`failed` y un `error`. Estos ejemplos y sus etiquetas son sintéticos, creados
por el asistente y sin revisión humana independiente; ilustran el cálculo,
no la calidad de un sistema real.

## Informe y ejecución propuestos

El informe JSON tendrá `schema_version: 1`, `evaluation_type: "retrieval"`,
`k`, hashes SHA-256 de los bytes de los tres archivos (`corpus_sha256`,
`dataset_sha256`, `outputs_sha256`), conteos globales, métricas y cobertura por
grupo, y resultados individuales en el orden de los casos. Cada resultado
individual identificará el caso, su grupo, estado, rango y código de error;
`rank` será `null` en negativos, errores y positivos sin coincidencia. El grupo
y el estado permiten distinguir esos resultados. No se imprimirán preguntas ni
listas de documentos. Los IDs y rangos pueden ser sensibles, y los hashes no
anonimizan contenidos predecibles ni prueban que las etiquetas sean correctas.

Para los mismos bytes y `k`, el informe será determinista: claves ordenadas,
escapes ASCII, separadores compactos y salto de línea LF, sin fecha ni IDs
aleatorios. `0` indicará que todos los casos pasan; `1`, al menos un `failed` o
`error`; `2`, entrada inválida o ejecución incompleta. `--help` terminará en
`0`. El informe válido se escribirá en stdout incluso con código `1`.

Se usa un módulo independiente, conservando sin cambios el comando de
coincidencia exacta existente:

```powershell
python -m evals_lab.retrieval --corpus examples/retrieval_corpus.jsonl --cases examples/retrieval_cases.jsonl --outputs examples/retrieval_outputs.jsonl --k 3
```

El comando utiliza los archivos de ejemplo incluidos en el proyecto. La lectura
JSONL se reutiliza mediante validadores específicos para registros con listas.
No se añadieron dependencias ni adaptadores a otros proyectos; el comportamiento
y el informe del comando existente se conservan.

La imagen Docker actual arranca el comando de coincidencia exacta mediante su
`ENTRYPOINT`. Para ejecutar el módulo de recuperación se invoca explícitamente
Python como punto de entrada, por ejemplo con `--entrypoint python` y los
argumentos `-S -m evals_lab.retrieval`, montando los datos de solo lectura. Las
comprobaciones Docker enumeran los módulos permitidos y verifican ambos modos.

## Verificación

Las pruebas cubren posiciones 1, 2 y `k`, coincidencias fuera de `k`, múltiples
relevantes, listas vacías y respuestas ausentes. También grupos vacíos y sus
denominadores `null`, cobertura, duplicados dentro y fuera de `k`, IDs
desconocidos, límites, codificación, campos inválidos y valores de `k` fuera de
rango. También comprueban informe determinista, hashes, códigos de salida,
ausencia de contenido y rutas en errores, y compatibilidad con el evaluador
existente. CI configura dos versiones de Python y las comprobaciones Docker.

La integración con RAG o Memory, la evaluación de generación y las llamadas a
modelos requieren decisiones posteriores.
