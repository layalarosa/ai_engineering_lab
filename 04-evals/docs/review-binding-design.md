# Diseño: vincular una revisión con su pregunta y respuesta

**Estado: implementado.** El evaluador vincula las revisiones versión 2 con la
pregunta y la respuesta registradas. Solo se actualizó el ejemplo sintético;
no se migraron revisiones reales.

## El problema actual

Antes de este cambio cada revisión contenía `response_sha256`, calculado sobre la línea JSONL
de su respuesta y fuentes. El evaluador comprueba ese hash antes de usar los
juicios. Sin embargo, una persona puede cambiar `input` en `cases.jsonl` y
mantener el mismo `id`, respuesta y revisión. El evaluador anterior aceptaba la
revisión y calculaba métricas aunque la pregunta fuera distinta. El informe registra
el hash del archivo completo de casos, pero no sabe con qué pregunta se creó
cada revisión.

El adaptador RAG comprueba que la pregunta del informe coincida con la del
archivo de casos **durante la conversión**. Esa comprobación no vincula una
revisión guardada con futuras versiones de la pregunta. Se necesita validar la
relación al evaluar, sin depender de que el operador compare hashes a mano.

## Contrato propuesto

La versión nueva de cada registro de revisión tendrá exactamente:

```text
id, schema_version, case_sha256, response_sha256, review_status, abstention, claims
```

`schema_version` será el entero `2`, no booleano. `case_sha256` será SHA-256
de los bytes exactos de **la línea JSONL del caso con el mismo ID**, sin el
terminador LF o CRLF. `response_sha256` conservará su definición actual: hash
de los bytes exactos de la línea JSONL de respuesta correspondiente, sin el
terminador. Ambos hashes serán 64 dígitos hexadecimales minúsculos.

Antes de calcular métricas, el evaluador comparará los dos hashes con las
líneas leídas, validará que los IDs correspondan y mantendrá las comprobaciones
existentes de formato, límites e intervalos. Una revisión completa **o
incompleta** requiere ambos vínculos válidos. Un hash incompatible, un campo
ausente o inválido, un ID desconocido, una versión no admitida o una respuesta
ausente con revisión harán que la entrada se rechace con código `2`, sin
informe y con un mensaje sanitizado. No se convertirá una revisión incompatible
en un fallo semántico ni en una revisión vacía.

El vínculo incluye el ID, la pregunta y su representación JSON exacta. Cambiar
espacios, orden de campos, escapes o normalización Unicode puede invalidarlo
aunque la pregunta parezca igual. Reordenar otras líneas o modificar otro caso
no invalida esta revisión individual; el hash del **archivo completo** sí
cambia y seguirá incluyéndose en el informe. Cambiar solo LF por CRLF no
cambia el hash de línea, porque se excluye el terminador, pero sí cambia el
hash del archivo. Se conservan los límites y reglas JSONL actuales, incluida
la prohibición de BOM y de una línea terminada en CR sin LF.

Un hash identifica bytes: no demuestra que el juicio sea correcto o
independiente, ni autentica al revisor o los datos. Quien pueda alterar los
archivos puede recalcular ambos hashes. Los hashes tampoco anonimizan preguntas
predecibles. El informe RAG original y su configuración deben conservarse por
separado cuando se necesite verificar su procedencia.

### Ejemplo verificable

Con la siguiente línea de caso en UTF-8, sin incluir el salto final en el hash:

```jsonl
{"id":"ejemplo","input":"¿Qué hace el sistema?"}
```

`case_sha256` es
`9885568edf5a1da21f2ce84ee15e5d78ae45545262c83d10db5e5541cf2e1f0b`.
Para la línea de respuesta del [ejemplo existente](../examples/grounding_outputs.jsonl),
`response_sha256` es
`e50ef270fe741d43c9549eb2b01d51675022df45ac1b2f4c3193c14489e40ac2`.
Una revisión todavía incompleta podría representarse así:

```jsonl
{"id":"ejemplo","schema_version":2,"case_sha256":"9885568edf5a1da21f2ce84ee15e5d78ae45545262c83d10db5e5541cf2e1f0b","response_sha256":"e50ef270fe741d43c9549eb2b01d51675022df45ac1b2f4c3193c14489e40ac2","review_status":"incomplete","abstention":null,"claims":[]}
```

Es un ejemplo de formato, no una revisión humana realizada. Si se cambia la
pregunta, la revisión se rechaza aunque su respuesta no cambie. Añadir el
nuevo hash no demuestra que se haya revisado la nueva pregunta: la persona
debe volver a revisar el caso antes de registrar una anotación nueva.

## Compatibilidad y migración explícita

Los registros existentes no tienen `schema_version` ni `case_sha256`; se
identifican como **formato anterior**, con exactamente las claves que aceptaba
el evaluador. La implementación es estricta por defecto:
rechazará esos registros con código `2`. Esto cambia deliberadamente el
comportamiento del comando de sustento para revisiones antiguas; no afecta a
los comandos de coincidencia exacta, recuperación ni a los adaptadores RAG.
Se actualizarán el ejemplo y la documentación para el nuevo formato.

Para inspeccionar archivos antiguos existe una opción explícita,
`--allow-unbound-reviews`. Permite leerlos junto a registros de versión 2,
siempre con IDs únicos. **No los convertirá ni los puntuará**: comprobará su
hash de respuesta y sus intervalos como hoy, pero los marcará
`review_unbound`, porque el archivo no permite comprobar qué pregunta se
revisó. Las afirmaciones podrán mostrarse para inspección con su estado de
vínculo, pero no se sumarán a las métricas ni contarán como revisión completa.
La ejecución terminará con código `1` mientras exista alguna revisión sin
vínculo, incluso si en el formato antiguo declaraba `complete`.

No se aceptarán formas ambiguas: por ejemplo, una revisión con
`case_sha256` pero sin `schema_version`, una versión desconocida o claves
adicionales no se tratará como antigua. El modo explícito no permite ignorar
un hash incorrecto de una revisión versión 2 ni un hash de respuesta
incorrecto. Un archivo de revisiones vacío sigue siendo válido y las respuestas
sin revisión siguen apareciendo como `review_missing`.

La migración exige que una persona contraste la pregunta y respuesta
originales, revise de nuevo los juicios cuando corresponda y genere un registro
versión 2 con los hashes de los bytes que revisó. No habrá un comando que
añada automáticamente un hash a revisiones antiguas como si demostrara que
fueron revisadas frente a la pregunta actual.

## Informe y métricas propuestas

Como cambia el significado de algunos conteos y se añade un estado, el informe
de `grounding` pasará de `schema_version: 1` a `schema_version: 2` para todas
las ejecuciones de la nueva implementación. Los consumidores deberán admitir
esa versión; no se presentará como salida idéntica a la actual.

- Se añade `review_unbound` al resumen y como estado por caso, y
  `claims_excluded_unbound` para contar sus afirmaciones excluidas.
- Cada resultado de caso indicará `case_binding`: `matched` cuando se validó
  el hash de una revisión versión 2, `absent` para una revisión antigua
  admitida explícitamente, y `null` cuando no haya revisión. `matched` indica
  solo coincidencia de bytes, no autenticidad ni calidad del juicio.
- El detalle tendrá `declared_review_status`, igual al `review_status` de la
  anotación cuando exista y `null` en otro caso. Para inspección, una revisión
  antigua conservará también su abstención y afirmaciones, pero su
  `supported_fraction` será `null`. Esos valores no participarán en `explicit_abstentions`,
  `complete_no_claims` ni en los conteos y métricas de afirmaciones puntuadas.
- `review_coverage` seguirá siendo revisiones completas puntuables divididas
  entre respuestas presentes: las antiguas sin vínculo quedarán en el
  denominador, pero no en el numerador. Las revisiones incompletas versión 2
  continuarán excluidas de las métricas de afirmaciones.
- Los casos sin salida seguirán como `missing_output`; los que tengan salida
  y no revisión, como `review_missing`. La suma de esos dos estados, revisiones
  completas, incompletas y no vinculadas será el total de casos.

El código `0` indicará que todas las respuestas y revisiones están completas
y vinculadas, sin garantizar que las afirmaciones estén sustentadas. El código
`1` indicará ausencias, revisiones incompletas o no vinculadas aceptadas con la
opción explícita. El código `2` indicará entrada inválida o fallo de ejecución;
`--help` seguirá devolviendo `0`. El informe conservará serialización
determinista y no imprimirá pregunta, respuesta ni texto de evidencia. Si se
interrumpe stdout durante la escritura, puede quedar una salida parcial que
deberá descartarse.

## Implementación y pruebas previstas

El lector JSONL ya puede calcular hashes de línea mediante `line_hashes`; se
propone usarlo también para los casos. El validador de revisiones tendrá que
reconocer explícitamente las dos formas exactas y mantener los mismos límites
de tamaño, tipos e intervalos. No se prevé introducir una base de datos, un
framework ni una dependencia externa. El adaptador RAG no necesita modificar
su salida: entrega respuestas que ya pueden vincularse por línea.

Las pruebas deberán verificar:

- Cambios en pregunta, ID, espacios, escapes Unicode o respuesta; comparación
  antes de emitir informe y sin filtrar contenido o rutas en errores.
- Mismo hash de línea con LF y CRLF, pero distinto hash de archivo; reordenar
  casos u otras respuestas sin invalidar líneas idénticas.
- Versión 2 válida, versiones desconocidas, booleanos donde se esperan enteros,
  claves extra, hashes mal formados, duplicados y límites existentes.
- Rechazo predeterminado de formato antiguo y diagnóstico explícito con
  `review_unbound`, código `1` y exclusión de todas sus afirmaciones; archivos
  mixtos, vacíos, incompletos y casos sin respuesta.
- Informes versión 2 deterministas, denominadores y códigos `0`, `1`, `2`,
  cierre de stdout, compatibilidad de los demás comandos y paridad Docker.

Se implementó este contrato y se actualizó el ejemplo sintético del
repositorio. No se migraron revisiones reales ni se anotaron automáticamente
respuestas de la línea base.
