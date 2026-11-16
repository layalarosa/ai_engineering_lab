# Prototipo de correcciones: desarrollo

Se implementó una candidata local que usa registros de origen y sustitución
proporcionados explícitamente. Puede seleccionar un único mensaje actual o
devolver un estado de abstención; rechaza vínculos estructuralmente inválidos.
No interpreta el contenido ni comprueba si los metadatos dicen la verdad. No
forma parte de la API pública ni modifica la persistencia de Memory.

## Ejecución

Desde `03-memory`, sin paquetes externos ni servicios:

```powershell
python -S experiments/corrections/run.py
python -S -m unittest discover -s tests -p test_corrections_prototype.py -v
python -S experiments/corrections/verify_freeze.py
```

El ejecutor muestra JSON con resultados y métricas por caso, categoría y
método; no imprime el contenido de los mensajes. Compara la candidata con la
recencia real y la búsqueda léxica del primer experimento. Los métodos de
referencia solo reciben la consulta textual y el historial. La candidata recibe
además la intención estructurada y los registros, por lo que las comparaciones
no aíslan el efecto del algoritmo del efecto de esa información adicional.

[development.json](development.json) contiene 24 consultas de desarrollo sobre
23 instantáneas: hay tres casos por cada una de las ocho categorías del diseño.
Una instantánea se reutiliza para dos consultas, con los mismos registros.
Historiales, registros, consultas y juicios están separados; la candidata no
recibe etiquetas ni resultados esperados. Las etiquetas se crearon junto con
los casos por el mismo asistente y no tienen revisión humana independiente.

Los 23 historiales tienen en total 40 registros sintéticos, incluido uno
deliberadamente inválido. Prepararlos exige identificar la clave, su fuente y,
en su caso, qué registro anterior sustituye. No se midió el tiempo de
preparación ni el coste de verificar su procedencia. El prototipo valida y
recorre los mensajes y registros por llamada; no se ha medido rendimiento de
producción, coste de almacenamiento ni mantenimiento de metadatos reales.

## Observaciones de desarrollo

La [instantánea de resultados](development-results.json) recoge los estados y
posiciones. Los 24 resultados de la candidata coinciden con los estados esperados
al escribir estos mismos casos: es una comprobación de contrato, no una medida
independiente de calidad.

| Observación | Resultado de la candidata |
| --- | --- |
| Selección con toda la evidencia requerida en casos resolubles | 11 de 14 |
| Abstención en casos válidos con evidencia pertinente | 8 de 20 |
| Salida en casos válidos sin evidencia pertinente | 0 de 3 |
| Casos válidos con contenido desactualizado seleccionado | 1 de 23 |
| Entrada con metadatos inválidos rechazada | 1 de 1 |

Para esta medición, un caso resoluble tiene una pregunta de un único dato actual,
evidencia suficiente en el historial y presupuesto adecuado, aunque falten
metadatos o el llamador haya declarado erróneamente que la intención no es
soportada. «Selección con toda la evidencia» no penaliza por sí sola un mensaje
adicional desactualizado; por eso se informa ese riesgo por separado. Los
errores en entradas válidas permanecen en el denominador de selección útil.

Las ocho abstenciones cuando había evidencia se reparten entre ambigüedad (3),
intención no soportada (3), metadatos ausentes (1) y presupuesto (1). Algunas
son el comportamiento previsto por el contrato. En `d17`, un vínculo falso
pero estructuralmente válido hace que la candidata seleccione una afirmación
desactualizada: la validación del grafo no detecta una relación semánticamente
incorrecta. En `d16`, la información pertinente carece de registro y la
candidata no la encuentra. En `d18`, una intención mal clasificada provoca
abstención pese a existir evidencia.

El informe incluye también la cobertura emparejada con ambos métodos anteriores
y sus errores, pero este conjunto se ha usado para construir y comprobar la
candidata. No demuestra que vaya a funcionar mejor con otras conversaciones ni
justifica incorporarla a la API. Una salida vacía tampoco prueba que una
aplicación o un modelo vaya a abstenerse de contestar.

## Congelación y siguiente paso

[freeze.json](freeze.json) registra los hashes de la candidata, el ejecutor,
los casos y resultados de desarrollo, sus pruebas, las referencias que utiliza
y el documento de diseño. Es un registro verificable de esta versión, no una
protección contra cambios ni una prueba de revisión independiente. No se han
creado ni inspeccionado los 24 casos reservados propuestos. Si se aprueba la
siguiente fase, deberán prepararse con escenarios nuevos y verificarse los
hashes antes de ejecutar la evaluación; si se ajusta la candidata después de
verlos, serán datos de desarrollo y hará falta otra reserva.
