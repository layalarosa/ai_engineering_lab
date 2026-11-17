# Diseño: evaluar la cobertura de información esperada

**Estado: agregador offline implementado.** Procesa juicios registrados sobre
los puntos que una respuesta aborda. No extrae criterios o juicios
automáticamente, no consulta modelos y conserva los evaluadores existentes.

## Qué problema resuelve

Una respuesta puede contener afirmaciones sustentadas y omitir un punto
importante de la pregunta. En la [ejecución RAG registrada](../eval_data/rag_baseline/run-2026-11-16-2315z/results.md),
la respuesta sobre pgvector menciona la búsqueda por distancia, pero no
explicita el almacenamiento de embeddings. Una omisión no es, por sí sola,
una afirmación falsa ni una afirmación sin sustento.

El enfoque propuesto descompone la información esperada en **puntos
verificables**, fijados antes de revisar las respuestas que se quieren
comparar. Una persona juzga si cada punto se aborda, se omite o queda incierto.
El programa valida las anotaciones y agrega recuentos; no verifica su
significado. A continuación se llama *cobertura de puntos esperados* a esa
medida, para no confundirla con corrección o exhaustividad general.

Los criterios son una referencia creada por personas: si faltan puntos, son
ambiguos o se redactan después de ver las respuestas, la medida puede ser
engañosa. Para una comparación válida hay que fijar criterios y reglas antes
de inspeccionar sus resultados, documentar su fundamento y revisar el desacuerdo.
El ejemplo de este documento es retrospectivo y sintético; sirve para aprender,
no es un conjunto independiente ni una comparación de sistemas.

## Tres juicios distintos

| Juicio | Significado para un punto esperado |
| --- | --- |
| `addressed` | La respuesta aborda el contenido con suficiente especificidad para identificar el punto. Se registra al menos un intervalo de respuesta. |
| `omitted` | Tras revisar la respuesta, no se encuentra contenido que aborde el punto. No hay intervalo de respuesta. |
| `uncertain` | El contenido es parcial, ambiguo o no permite decidir si aborda el punto. Se pueden registrar intervalos de contexto. |

`addressed` mide **cobertura temática**, no si la afirmación coincide con el
criterio o con la realidad. Por ejemplo, si el punto es «pgvector puede
almacenar embeddings», una respuesta que diga «pgvector no puede almacenar
embeddings» aborda ese punto, aunque lo contradiga. El sustento y la corrección
de esa afirmación necesitan una revisión separada. Un rechazo explícito o una
respuesta incorrecta no deben convertirse en información correcta solo por
haber abordado el tema.

Cada criterio debe ser lo bastante concreto para poder juzgarlo por separado;
un criterio compuesto y una respuesta que solo cubre una parte requieren
dividirlo antes de evaluar o marcar `uncertain`. Los mismos intervalos pueden
ayudar a juzgar varios puntos. Si se desconoce qué puntos deberían exigirse,
el caso se deja sin criterios; no se registra como completo por defecto.

Esta primera versión evalúa únicamente **puntos de contenido**. Una abstención
apropiada es un comportamiento distinto: un caso cuya expectativa sea
únicamente reconocer falta de evidencia puede quedar sin criterios de
contenido y se informará como no etiquetado para esta métrica. El campo de
abstención del evaluador de sustento puede describir lo observado, pero por sí
solo no decide si el rechazo fue apropiado. La evaluación de comportamiento
requerirá un contrato posterior.

## Entradas propuestas

Tres archivos JSONL UTF-8 sin BOM y sin acceso a servicios:

1. **Casos con criterios:** cada línea contiene exactamente `id`, `input` y
   `expected_items`. La lista admite de 0 a 20 objetos, cada uno con
   exactamente `item_id` y `description`. Los IDs de puntos son únicos dentro
   del caso. Una lista vacía indica un caso **sin criterios de contenido**,
   no una respuesta completa. Hay entre 1 y 1.000 casos.
2. **Respuestas y fuentes:** se reutiliza el formato exacto de salida del
   adaptador RAG para sustento: `id`, `answer` y `sources`, con cada fuente
   `{source_id, document_id, chunk_index, text}`. La completitud propuesta usa
   `answer` y conserva las fuentes en la instantánea vinculada; no las juzga
   ni consulta otros documentos. Admite de 0 a 1.000 respuestas.
3. **Revisiones de cobertura:** cada línea contiene exactamente `id`,
   `schema_version`, `case_sha256`, `response_sha256`, `review_status` e
   `items`. `schema_version` es el entero no booleano `1` de **este nuevo
   contrato**; no es la versión de una revisión de sustento. La lista admite
   de 0 a 20 juicios y cada juicio contiene exactamente `item_id`, `verdict`
   y `answer_spans`.

Los criterios de completitud cambian la estructura del caso: los archivos de
casos del evaluador de sustento no son intercambiables sin una preparación
explícita. Las respuestas sí pueden ser exactamente el mismo archivo. Los
juicios de sustento y de cobertura son archivos separados; no se infieren unos
de otros.

`review_status` será `complete` o `incomplete`. Una revisión completa debe
incluir **exactamente un juicio por cada punto esperado**; puede declarar
`uncertain` cuando el revisor no puede resolver un punto. Una incompleta puede
incluir un subconjunto sin duplicados: sus juicios se mostrarán para
inspección, pero todos quedarán fuera de las métricas agregadas. Ninguna
revisión puede referirse a un punto desconocido, a un caso sin respuesta o a
un caso con lista de criterios vacía. Esos errores invalidan la entrada; no se
rellenan automáticamente juicios omitidos.

Cada elemento de `answer_spans` contiene exactamente `start` y `end`, índices
enteros no booleanos de puntos de código Unicode Python, desde cero y con final
exclusivo. El intervalo debe ser no vacío y caber dentro de `answer`; no se
normaliza texto. `addressed` requiere entre 1 y 20 intervalos, `omitted` requiere
una lista vacía y `uncertain` admite de 0 a 20. Se permiten solapamientos. Un
intervalo válido es una referencia inspeccionable, no prueba que el juicio sea
correcto.

Los IDs siguen `[a-z0-9][a-z0-9_-]{0,63}`. `input` y `answer` admiten hasta
4.000 puntos de código y pueden estar vacíos; `description` admite hasta 4.000
y no puede estar vacía ni ser solo espacios. Los demás requisitos de fuentes
son los ya implementados por el evaluador de sustento. Se rechazan claves
adicionales o duplicadas, IDs repetidos o desconocidos, campos ausentes, tipos
incorrectos, UTF-8 inválido y sustitutos Unicode no codificables. Se conservan
los límites de 2 MiB por archivo y 65.536 bytes por línea sin LF o CRLF; esos
límites también se aplican cuando las listas individuales son válidas.

## Vinculación de una revisión

`case_sha256` es el SHA-256 de los bytes exactos de la línea JSONL del caso,
sin terminador LF o CRLF; vincula pregunta, IDs y descripciones de todos sus
puntos. `response_sha256` se calcula de igual forma sobre la línea de respuesta
completa, incluidas las fuentes. Ambos son 64 caracteres hexadecimales
minúsculos. Si cambia la pregunta, un criterio, la respuesta, una fuente o su
representación JSON, la revisión debe volver a comprobarse y el hash anterior
será rechazado antes de publicar informe. Cambiar otro caso o el orden de las
líneas no invalida una revisión individual; cambiará el hash del archivo entero,
que también se registrará.

LF y CRLF producen el mismo hash de línea sin terminador, pero distintos
hashes de archivo. Esta vinculación no autentica al revisor, no demuestra que
una revisión sea independiente y no anonimiza textos predecibles. Las
anotaciones de sustento versión 2 no son anotaciones de cobertura; aunque
ambas incluyan hashes, sus esquemas y juicios son diferentes.

## Ejemplo: respuesta de pgvector

El siguiente caso es una **selección didáctica retrospectiva** de dos puntos;
no pretende demostrar que sean todos los que podrían exigirse. La línea debe
estar en UTF-8 exactamente como se muestra para que coincida el hash:

```jsonl
{"id":"pgvector","input":"¿Qué permite hacer pgvector con embeddings?","expected_items":[{"item_id":"almacenar","description":"Pgvector permite almacenar embeddings."},{"item_id":"buscar","description":"Pgvector permite buscar embeddings por distancia vectorial."}]}
```

Para esa línea, `case_sha256` es
`b1ac481adea25b176e2b5365f8c9cc1a03f3931830b74926c35758ca6bc56563`.
Se usa la respuesta `pgvector` de la [instantánea de respuestas y fuentes](../eval_data/rag_baseline/grounding-2026-11-16-2315z/outputs.jsonl):

> Permite realizar búsquedas por distancia vectorial y reutilizar vectores sin calcularlos de nuevo para cada consulta.

Su línea JSONL exacta tiene `response_sha256`
`146d4c7267506cbc9901f46f6ecd2c20b611afb0a3f34d88a5a769936af22ab2`.
Una anotación ilustrativa sería:

```jsonl
{"id":"pgvector","schema_version":1,"case_sha256":"b1ac481adea25b176e2b5365f8c9cc1a03f3931830b74926c35758ca6bc56563","response_sha256":"146d4c7267506cbc9901f46f6ecd2c20b611afb0a3f34d88a5a769936af22ab2","review_status":"complete","items":[{"item_id":"almacenar","verdict":"omitted","answer_spans":[]},{"item_id":"buscar","verdict":"addressed","answer_spans":[{"start":0,"end":50}]}]}
```

El intervalo `[0:50]` identifica «Permite realizar búsquedas por distancia
vectorial». El ejemplo considera la pregunta como contexto para entender el
sujeto y el objeto. La respuesta no enuncia explícitamente el almacenamiento;
un revisor podría discutir si lo infiere de «reutilizar». Es un juicio del
asistente sin revisión humana independiente. Los dos criterios darían cobertura
`1/2`; ese número no demuestra que la respuesta sea correcta ni que cubra
todas las expectativas posibles. La revisión de sustento de `pgvector` sigue
incompleta y sus juicios no se alteran.

## Resultado propuesto

Cada caso tendrá un estado exclusivo: `missing_output`, `unlabeled`,
`review_missing`, `review_incomplete` o `review_complete`. Primero se registra
una salida ausente como `missing_output`; si existe respuesta pero no hay
criterios, se registra `unlabeled`. No se permite revisión para casos sin
respuesta o sin criterios. También se informará `unlabeled_cases_total`
independientemente de esos estados, para no ocultar casos que carecen tanto de
criterios como de respuesta.

El informe conservará resultados individuales con IDs, juicios e intervalos,
pero no repetirá preguntas, respuestas, descripciones de criterios ni fuentes.
Incluirá hashes de los tres archivos, recuentos y estos denominadores:

- `output_coverage = outputs_present / total_cases`.
- `review_coverage = review_complete / eligible_cases`, donde `eligible_cases`
  tiene criterios y respuesta; es `null` si no hay casos elegibles. Un caso con
  revisión completa pero algún juicio `uncertain` cuenta como revisión
  registrada, no como una decisión resuelta.
- Conteos de puntos `addressed`, `omitted` y `uncertain` **solo en revisiones
  completas**, y `items_excluded_incomplete` para los juicios provisionales.
- `addressed_fraction = addressed / (addressed + omitted + uncertain)`, o
  `null` si no se puntuó ningún punto. `uncertain` permanece visible en su
  propio conteo: entrar en el denominador no lo convierte en una omisión.
  Opcionalmente se informa `addressed_fraction_decided = addressed /
  (addressed + omitted)`, `null` cuando no hay juicios decididos; debe mostrarse
  siempre junto a los conteos de inciertos y la fracción anterior.

Para una revisión completa, `all_addressed` es `true` si todos sus puntos son
`addressed`, `false` si al menos uno es `omitted`, y `null` si no hay omisiones
pero hay algún `uncertain`. En casos sin criterios, sin respuesta, sin revisión
o con revisión incompleta también es `null`. No se denomina «respuesta
correcta»: una negación explícita puede abordar un punto y ser incorrecta.

Se propone código `0` si todos los casos tienen respuesta, criterios y
revisiones completas sin juicios `uncertain`, incluso si existen omisiones;
`1` si faltan respuestas o criterios, faltan revisiones, hay revisiones
incompletas o juicios inciertos; `2` para entradas inválidas o errores de
ejecución. Una evaluación descriptiva con omisiones no equivale
automáticamente a una barrera de despliegue. `--help` termina con `0`.

Los informes serán deterministas para los mismos bytes, con claves ordenadas,
escapes ASCII, separadores compactos y LF final. Ningún informe se publica si
las entradas no son válidas; un fallo de stdout durante la escritura puede
dejar una salida parcial que debe descartarse. Mensajes de error sin rutas ni
contenido. IDs, intervalos y hashes pueden seguir siendo sensibles.

## Implementación y verificación

La implementación reutiliza el lector JSONL acotado y la validación de
instantáneas de respuesta y hashes ya necesarios para sustento, con validadores
propios para criterios y juicios. No requiere framework,
base de datos, llamadas de red ni un adaptador RAG nuevo. No se deben cambiar
los contratos existentes para facilitar su implementación.

Las pruebas cubren el ejemplo y una negación explícita que aborda
un punto pero puede ser falsa; casos sin criterios; respuestas o revisiones
ausentes; revisiones parciales e inciertas; puntos repetidos o desconocidos;
límites y duplicados; intervalos Unicode y CRLF; hashes de caso y respuesta
caducados; denominadores vacíos y estados; códigos de salida, errores
sanitizados, salida determinista y compatibilidad Docker con los modos previos.

El agregador incluye ejemplos sintéticos. La anotación de la línea base como
conjunto de evaluación y cualquier juez automático requieren decisiones
posteriores.
