# Evaluación de correcciones con casos reservados

La candidata **no cumple todos los criterios de adopción del experimento**. En
un caso sin respuesta seleccionó un mensaje que un registro falso identificaba
como pertinente. También seleccionó información desactualizada cuando recibió
un vínculo falso de sustitución. La búsqueda estructurada permanece como
prototipo local.

## Cómo se evaluó

Se prepararon 24 casos sintéticos nuevos, tres por cada categoría del
[diseño congelado](../../docs/corrections-abstention-design.md). El corpus está
en [heldout.json](heldout.json) y el detalle de la ejecución en
[heldout-results.json](heldout-results.json). Incluye 23 casos con entradas
válidas y uno con un vínculo inválido deliberado. De los válidos, cuatro no
contienen evidencia pertinente; dos contienen metadatos falsos y uno tiene
metadatos incompletos.

El mismo asistente que conocía el prototipo y los resultados previos creó y
etiquetó estos casos. Se usaron escenarios nuevos, pero comparten estructuras
con los ejemplos de desarrollo, y no hubo revisión humana independiente. Por
eso se trata de una **reserva didáctica no ciega**, no de una evaluación
independiente ni representativa de conversaciones reales. Las etiquetas y la
correspondencia entre consulta textual e intención estructurada son revisables
en el corpus.

Antes de crear el corpus se comprobó la [congelación del prototipo](freeze.json)
y se registraron los hashes del evaluador y sus pruebas en el
[plan de evaluación](evaluation-plan.json). La ejecución verifica ambos
registros y no modifica el prototipo. Son archivos locales: los hashes permiten
detectar cambios frente a estos registros, pero los registros también se pueden
modificar y no prueban por sí solos una revisión independiente ni la fecha de
creación. El documento de diseño y el informe de desarrollo incluidos en la
congelación describen el estado **anterior** a crear esta reserva.

La candidata recibe claves y vínculos de origen adicionales que no reciben
recencia ni búsqueda léxica. Los 24 historiales contienen 41 registros
sintéticos, incluido uno inválido; no se midió el tiempo necesario para
crearlos, revisarlos o mantenerlos. No se evaluaron un modelo generativo, la
autorización de los emisores ni el rendimiento de una base real.

Para reproducir desde `03-memory`, sin dependencias externas:

```powershell
python -S experiments/corrections/verify_freeze.py
python -S experiments/corrections/evaluate_holdout.py
python -S -m unittest discover -s tests -p test_corrections_holdout_evaluator.py -v
```

El evaluador emite JSON con hashes, criterios, métricas por categoría y
posiciones por caso; no imprime el contenido de los mensajes. Repetir la
ejecución produjo la misma selección; la fecha del informe cambia.

## Resultados

| Criterio acordado | Resultado observado |
| --- | --- |
| Seleccionar evidencia vigente en casos resolubles con metadatos correctos | 11 de 11 cumplieron. |
| Abstenerse o rechazar la entrada en los casos etiquetados para ello | 7 de 7 cumplieron. |
| No seleccionar mensajes cuando el historial no contiene respuesta | 3 de 4 cumplieron; falló `h18`. |
| Respetar presupuestos y no modificar las entradas | Cumplido en los 24 casos. |

En `h18`, el historial menciona una exposición pero no su fecha; un registro
falso atribuye una fecha al mensaje. La candidata lo devuelve porque confía en
el registro. En `h17`, un vínculo falso apunta a una versión antigua y la
candidata selecciona esa versión. En `h16`, un mensaje pertinente no tiene
registro y la candidata devuelve `sin_evidencia_registrada`. Estos casos
ilustran límites distintos: el formato de un registro puede ser válido aunque
su contenido sea falso o incompleto.

| Métrica sobre los casos válidos | Recencia | Léxica | Estructurada |
| --- | --- | --- | --- |
| Selección con toda la evidencia requerida en casos resolubles | 11/13 | 12/13 | 11/13 |
| Abstención cuando había evidencia pertinente | 0/19 | 1/19 | 7/19 |
| Salida en casos sin evidencia pertinente | 4/4 | 4/4 | 1/4 |
| Selección de algún mensaje desactualizado | 3/23 | 5/23 | 1/23 |

La primera fila mide si están presentes todos los mensajes pertinentes; no
penaliza por sí sola los mensajes adicionales desactualizados. Estos se
informan en la última fila. Algunas abstenciones son exigidas por el contrato,
como ante información ambigua o una consulta histórica. Recencia no usa la
consulta: su salida en un caso sin respuesta no es un error de su contrato. En
`h21` produjo el error previsto porque el último mensaje superó el presupuesto;
el informe lo muestra separadamente y lo excluye de las comparaciones
emparejadas donde corresponde.

Estos resultados muestran el coste de la abstención y la dependencia de los
metadatos, no una mejora generalizable. La candidata estructurada recibe más
información de entrada, las muestras son pequeñas y las categorías fueron
creadas con conocimiento del diseño. Aun si se cumplieran todos los criterios,
haría falta una evaluación más amplia y revisión independiente antes de
concluir que es apropiada para usuarios reales.
