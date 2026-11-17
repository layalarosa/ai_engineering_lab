# Diseño: evaluar el sustento de afirmaciones

**Estado: agregador offline implementado.** Procesa juicios previamente
registrados; los ejemplos incluidos son sintéticos y no tienen revisión humana
independiente. No es un juez automático, no llama a un modelo y conserva los
evaluadores existentes.

## Problema y alcance

Recuperar un documento pertinente no garantiza que una respuesta esté apoyada
por él. En la [ejecución local](../eval_data/rag_baseline/run-2026-11-16-2315z/results.md),
el documento esperado sobre pgvector apareció primero, pero la respuesta omitió
la capacidad de almacenar embeddings. Esa omisión es un problema de
**completitud**; por sí sola no demuestra que una afirmación de la respuesta
esté contradicha o carezca de sustento.

El sustento pregunta si cada afirmación de la respuesta se sigue de los
fragmentos disponibles en esa ejecución. No demuestra que una fuente sea
verdadera, que el modelo la haya consultado causalmente ni que la respuesta
cubra toda la pregunta. Tampoco evalúa si una cita aparece en la respuesta o
apunta a la fuente correcta. Si hay una afirmación sin evaluar, no se supone
que esté sustentada.

La primera versión separaría responsabilidades: una persona identifica
afirmaciones y evidencia, y el programa valida referencias y calcula
estadísticas. El programa **no puede verificar semánticamente** que el juicio,
la selección de afirmaciones o el alcance de una revisión sean correctos.
Para estudios posteriores, el desacuerdo entre revisores y el procedimiento de
adjudicación deben registrarse; este MVP no calculará acuerdo ni seleccionará
automáticamente un juicio ganador.

## Reglas para revisar

Una afirmación es una proposición verificable expresada por la respuesta. El
revisor intenta dividir las afirmaciones compuestas en unidades que puedan
juzgarse por separado, conserva el contexto necesario para resolver sujetos o
pronombres y registra el fragmento de respuesta que la originó. Puede redactar
una proposición autónoma fiel a ese texto; no debe añadir precisión, cantidad,
causalidad o alcance que la respuesta no exprese. Los intervalos de respuesta
pueden solaparse cuando dos proposiciones dependen de las mismas palabras.

La revisión considera **solo las fuentes entregadas con esa respuesta**, no el
conocimiento externo ni todos los documentos del corpus. Una ausencia de
evidencia no demuestra que la afirmación sea falsa. Si las fuentes están
truncadas, faltan o el revisor no puede realizar el juicio con el material
disponible, marca la revisión incompleta en vez de inventar una etiqueta.
Una respuesta puede no contener afirmaciones verificables; un rechazo explícito
puede registrarse por separado, sin asumir que sea apropiado para la pregunta.

Por afirmación, el revisor identifica intervalos de fuentes y su relación:

| Relación del intervalo | Significado |
| --- | --- |
| `supports` | El contenido, con el contexto disponible, apoya la proposición completa. |
| `contradicts` | El contenido contradice la proposición completa. |
| `context` | El intervalo se examinó y aporta contexto, pero no basta por sí solo para sostenerla ni contradecirla. |

El resultado derivado de esas relaciones es `supported` si hay apoyo y no
contradicción; `contradicted` si hay contradicción y no apoyo; `conflicting` si
hay ambas; `insufficient` si no hay ninguna de las dos. Las relaciones
`context` no cambian el resultado. La lista vacía también produce
`insufficient`, pero el revisor solo debe usarla después de examinar las
fuentes disponibles. Se pueden necesitar varios intervalos para un juicio;
que existan referencias no comprueba que realmente apoyen la afirmación.

## Entradas propuestas

Tres archivos JSONL UTF-8 sin BOM, independientes de RAG y sin acceso a red:

1. **Casos:** `{ "id", "input" }`. `input` es la pregunta para revisión, no
   se envía a un modelo. Entre 1 y 1.000 casos, IDs únicos.
2. **Respuestas y fuentes:** `{ "id", "answer", "sources" }`. Cada fuente
   contiene exactamente `{ "source_id", "document_id", "chunk_index", "text" }`.
   `source_id` identifica el fragmento **dentro del caso** y es único ahí;
   distintos fragmentos del mismo documento se conservan por separado. Puede
   haber de 0 a 20 fuentes. `chunk_index` es entero no booleano de 0 a 999.
   El campo `answer` puede ser una cadena vacía; eso no equivale a que falte
   el registro de respuesta.
3. **Revisiones versión 2:** `{ "id", "schema_version", "case_sha256", "response_sha256", "review_status", "abstention", "claims" }`.
   Cada caso tiene como máximo una revisión y solo puede revisarse si existe
   su respuesta. `schema_version` es el entero `2` y `case_sha256` vincula la
   línea exacta del caso, sin terminador. `response_sha256` es SHA-256 de los **bytes exactos de la
   línea JSON de esa respuesta**, sin terminador LF o CRLF. Se verifica antes
   de aceptar la revisión, para evitar aplicar intervalos a contenido distinto.
   Un cambio de espacios o de representación JSON también cambia el hash;
   hay que volver a vincular y revisar el juicio. El informe agregará hashes
   de bytes completos de los tres archivos.

`review_status` es `complete` o `incomplete`: significa que el revisor declara
haber terminado, no que el programa pueda comprobar exhaustividad. En una
revisión completa, `abstention` es booleano e indica un rechazo explícito; en
una incompleta puede ser booleano o `null`. El rechazo puede coexistir con
afirmaciones verificables. La lista `claims` admite de 0 a 20 afirmaciones;
una revisión completa con lista vacía queda como **revisada sin
afirmaciones**, no como sustentada ni necesariamente correcta. Las
afirmaciones registradas en una revisión incompleta se conservarán para
inspección, pero no entrarán en las métricas agregadas.

Cada afirmación tiene exactamente `{ "claim_id", "claim", "answer_span", "evidence" }`.
`claim` es la proposición autónoma redactada por el revisor; no es una fuente
adicional. `answer_span` tiene `{ "start", "end" }`. Cada elemento de
`evidence` tiene `{ "source_id", "start", "end", "relation" }`, con un
máximo de 20 referencias por afirmación. Los intervalos se expresan como
índices de **puntos de código Unicode de Python**, desde cero, con extremo
final exclusivo: `texto[start:end]`. No son bytes UTF-8 ni índices UTF-16.
Cada intervalo debe ser no vacío y estar dentro del texto correspondiente. El
programa comprueba límites y referencias, pero no que la cita demuestre el
juicio. No se normaliza Unicode ni se alteran espacios para aplicar índices.

Los IDs de caso, afirmación y fuente cumplen
`[a-z0-9][a-z0-9_-]{0,63}`; los de documento,
`[A-Za-z0-9_-]{1,64}`. Todos distinguen mayúsculas. Los IDs de afirmación son
únicos dentro de su caso. Los textos `input`, `answer`, `claim` y `text` tienen
hasta 4.000 puntos de código codificables en UTF-8; `claim` no puede estar
vacío ni compuesto solo de espacios. En los demás se permite cadena vacía;
una fuente vacía no puede aportar un intervalo no vacío. Las respuestas y
revisiones admiten entre 0 y 1.000 registros; ninguna puede referirse a un ID
de caso inexistente. Las fuentes se conservan como instantánea: `document_id`
es metadato, no se consulta ni verifica un corpus externo.

Se mantienen los límites de 2 MiB por archivo y 65.536 bytes por línea sin
terminador; ambos se aplican aunque los límites individuales permitan una
combinación mayor. Se rechazan líneas vacías, claves duplicadas, campos
adicionales o ausentes, UTF-8 inválido, sustitutos Unicode no codificables,
tipos incorrectos, índices booleanos o fuera de rango, IDs duplicados o
desconocidos y hashes incompatibles. Para estos registros se necesita ampliar
la lectura actual: hoy rechaza todos los números JSON, mientras que este
contrato requiere índices enteros. Se mantendrán acotados el análisis y los
errores; no se cambiará el comportamiento de los evaluadores actuales.

## Ejemplo de anotación

Caso:

```jsonl
{"id":"ejemplo","input":"¿Qué hace el sistema?"}
```

Respuesta y fragmento, en **una sola línea UTF-8**, exactamente como se muestra:

```jsonl
{"id":"ejemplo","answer":"El sistema almacena vectores y cifra los datos.","sources":[{"source_id":"s1","document_id":"manual","chunk_index":0,"text":"El sistema almacena vectores y permite buscarlos por distancia."}]}
```

Una revisión ilustrativa para esos bytes exactos, también en una línea:

```jsonl
{"id":"ejemplo","schema_version":2,"case_sha256":"9885568edf5a1da21f2ce84ee15e5d78ae45545262c83d10db5e5541cf2e1f0b","response_sha256":"e50ef270fe741d43c9549eb2b01d51675022df45ac1b2f4c3193c14489e40ac2","review_status":"complete","abstention":false,"claims":[{"claim_id":"c1","claim":"El sistema almacena vectores.","answer_span":{"start":0,"end":28},"evidence":[{"source_id":"s1","start":0,"end":28,"relation":"supports"}]},{"claim_id":"c2","claim":"El sistema cifra los datos.","answer_span":{"start":31,"end":46},"evidence":[]}]}
```

El primer intervalo de respuesta es `El sistema almacena vectores`; el segundo
es `cifra los datos`. El primero se juzga `supported`; el segundo,
`insufficient`, porque la fuente no menciona cifrado. La proporción de
afirmaciones sustentadas es `1/2`, no una probabilidad de que la respuesta sea
verdadera. Los datos y juicios de este ejemplo son sintéticos y no tienen
revisión humana independiente.

## Informe, denominadores y errores

El informe no repetirá preguntas, respuestas, proposiciones ni textos de
fuentes; sí contendrá IDs, estado de revisión, resultado de cada afirmación y
referencias de intervalos, además de hashes y recuentos. IDs, rangos y hashes
pueden ser sensibles; los hashes no anonimizan contenidos predecibles. La
salida será determinista para los mismos bytes: claves ordenadas, escapes
ASCII, separadores compactos y un LF final, sin reloj ni identificadores
aleatorios. Una entrada inválida impide publicar informe; una rotura de stdout
durante la escritura puede dejar una salida parcial que debe descartarse.

El informe usa `schema_version: 2` e incluye `total_cases`, `outputs_present`,
`missing_output`, `review_complete`, `review_incomplete`, `review_missing` y
`review_unbound`, además de:

- `output_coverage = outputs_present / total_cases`.
- `review_coverage = review_complete / outputs_present`; `null` si no hay
  respuestas. Una respuesta sin revisión no cuenta como revisada.
- `complete_no_claims` y `explicit_abstentions`, contabilizados solo en
  revisiones completas, sin decidir si esas respuestas son correctas.
- Conteos de `supported`, `contradicted`, `conflicting` e `insufficient`,
  usando solo afirmaciones de revisiones completas. Se informa además el número
  de afirmaciones excluidas por revisión incompleta o sin vínculo.
- `supported_fraction = supported / total_claims_scored`, o `null` cuando el
  denominador es cero. Una afirmación en conflicto no se cuenta como
  inequívocamente sustentada. Se mostrarán todos los denominadores junto con
  la puntuación y los resultados por caso; es una agregación por afirmación,
  susceptible a cómo el revisor las divide.

Un caso sin respuesta produce `missing_output`. Una respuesta sin revisión
produce `review_missing`; una revisión incompleta produce `review_incomplete`.
No se convierten en afirmaciones no sustentadas ni se suman a los denominadores
de afirmaciones. Una revisión completa sin afirmaciones produce una tasa por
caso `null`. El revisor puede haber omitido una afirmación pese a marcar
`complete`: la cobertura registra el estado declarado, no prueba exhaustividad.
Las revisiones antiguas sin vínculo de caso se rechazan por defecto; con
`--allow-unbound-reviews` se muestran como `review_unbound`, sin puntuarlas y
con código `1`. El [contrato de vinculación](review-binding-design.md) explica
la compatibilidad y las limitaciones de los hashes.

Se propone código `0` para una evaluación válida con todas las respuestas y
revisiones completas, aun si hay afirmaciones contradichas o sin evidencia;
`1` si falta una respuesta, una revisión, hay una revisión incompleta o se
inspecciona una revisión antigua sin vínculo; y `2`
si los datos son inválidos o no se puede completar el cálculo. La presencia de
`contradicted` no genera automáticamente una barrera de despliegue: cualquier
criterio de aceptación se definirá por separado. `--help` terminará con `0`.

## Relación con RAG y evolución

Este contrato conserva cada fragmento y su texto; el adaptador de recuperación
existente deduplica a nivel de documento y descarta textos, por lo que su salida
**no sirve** como fuente de evidencia para este evaluador. Una futura
integración requiere un adaptador explícito desde el informe RAG que preserve
la identidad y los valores de texto, y validación de fuentes y anotaciones. El
[adaptador de integración](rag-grounding-integration-design.md) convierte
informes ya registrados. Tampoco se guardan automáticamente
respuestas reales ni se enviarán a servicios externos.

El agregador utiliza anotaciones registradas, sin extracción automática de
afirmaciones. Las pruebas cubren intervalos Unicode y límites, evidencia contradictoria o
insuficiente, respuestas vacías o ausentes, revisiones faltantes e incompletas,
respuestas sin afirmaciones, hashes caducados, errores sanitizados y métricas
con denominadores vacíos. El [vínculo de pregunta y respuesta](review-binding-design.md)
ya está implementado para revisiones versión 2. Más adelante podría evaluarse un juez automático
frente a un conjunto de referencia humano revisado e independiente; no se
adoptará un framework hasta tener una necesidad concreta y una comparación.
