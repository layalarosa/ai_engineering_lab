# Experimento inicial de recuperación

La candidata léxica encontró más mensajes relevantes en este corpus pequeño,
pero también devolvió preferencias desactualizadas y mensajes en un caso sin
respuesta. **No cumple los criterios propuestos para adoptarla.** La recencia
sigue siendo la única selección disponible en la API de Memory.

## Reproducir

Desde `03-memory`, con Python 3.10 o superior:

```powershell
python -S experiments/retrieval/run.py
python -S -m unittest discover -s tests -p test_retrieval_experiment.py -v
```

El primer comando imprime un informe JSON con métricas por partición y categoría,
posiciones seleccionadas, etiquetas y errores por caso. No incluye los textos
de los mensajes. El segundo comprueba fórmulas, validación, presupuesto,
duplicados, Unicode, NUL y ejecución independiente. No se requieren PostgreSQL,
Docker, modelos ni paquetes externos.

[cases.json](cases.json) contiene 36 historiales sintéticos y etiquetas
exhaustivas por mensaje; cada caso tiene una razón de su clasificación. Se
reparten en 24 casos de desarrollo y 12 reservados, con cuatro y dos por
categoría respectivamente. El corpus y los juicios fueron creados por el mismo
asistente, sin revisión humana independiente. La comprobación de familias
verifica los identificadores registrados, pero no puede demostrar que los
escenarios sean estadísticamente independientes. Por ello los resultados son
provisionales y pueden reflejar sesgos de quien creó los casos.

La instantánea [results.json](results.json) se obtuvo el 16 de noviembre de
2026 con Python 3.10.11 en Windows. Incluye los hashes SHA-256 del corpus y del
ejecutor; un informe nuevo debería conservar los mismos resultados de selección
si ambos archivos no cambian. Los tiempos y datos del entorno pueden variar.
El diseño, reglas de puntuación y criterios están en
[retrieval-evaluation-design.md](../../docs/retrieval-evaluation-design.md).

## Resultados

La cobertura es la media, por consulta con mensajes relevantes, de la fracción
de esos mensajes recuperados. La comparación emparejada usa las mismas
consultas en las que ambos métodos terminaron sin error.

| Partición | Recencia: cobertura | Léxica: cobertura | Diferencia emparejada |
| --- | --- | --- | --- |
| Desarrollo | 44,7 % (19 casos) | 95 % (20 casos) | +50 puntos porcentuales (19 casos) |
| Reservada | 35 % (10 casos) | 90 % (10 casos) | +55 puntos porcentuales (10 casos) |

En desarrollo, recencia produjo un error porque el último mensaje superaba el
presupuesto: el caso `lim-dev-03` se excluye de su cobertura y de la diferencia
emparejada. La candidata terminó los 24 casos. La comparación de las dos medias
de desarrollo por sí sola usa denominadores distintos; la diferencia emparejada
evita esa comparación desigual. En la partición reservada no hubo errores.

| Observación | Desarrollo | Reservada |
| --- | --- | --- |
| Correcciones con mensaje desactualizado devuelto por léxica | 4 de 4 | 2 de 2 |
| Correcciones con mensaje desactualizado devuelto por recencia | 0 de 4 | 0 de 2 |
| Casos sin respuesta con salida léxica no vacía | 1 de 4 | 1 de 2 |
| Casos sin respuesta con salida por recencia | 4 de 4 | 2 de 2 |

La recencia ignora la consulta y entregar contenido en un caso sin respuesta no
es un error de su contrato. En `cor-res-01` y `cor-res-02`, léxica entrega tanto
la preferencia vigente como la anterior, mientras recencia omite ambas. En
`sin-res-02`, léxica encuentra palabras compartidas aunque el historial no
contiene el precio preguntado. En `var-res-02` devuelve vacío para una consulta
cuya respuesta se expresa con otras palabras.

La mejora emparejada reservada supera el umbral de 20 puntos porcentuales y no
se detectaron errores de la candidata ni violaciones de presupuesto. Sin
embargo, falla las condiciones de no devolver información desactualizada en
correcciones y de devolver vacío en todos los casos reservados sin respuesta.
Esos subgrupos tienen solo dos casos cada uno: incluso si se cumplieran los
umbrales, no demostrarían que el sistema funciona para usuarios reales.

La instantánea repite cada selección 100 veces por caso. En el conjunto
reservado, los tiempos agrupados observados fueron p50/p95 de 2,9/3,1 µs para
recencia y 9,7/16,3 µs para léxica. Son mediciones locales de funciones en
proceso: recencia utiliza una instancia ya cargada; léxica vuelve a normalizar
consulta e historial en cada llamada. No incluyen carga de historial, base de
datos, red ni modelos y no son una prueba de rendimiento de producción. Las
repeticiones del mismo caso no son consultas independientes.

Este experimento solo mide selección de mensajes. No comprueba generación,
veracidad, seguridad frente a instrucciones en el texto ni uso del contexto por
un modelo. Antes de ajustar una nueva candidata hay que definir y reservar
otros casos: los actuales ya se han observado y dejan de ser una evaluación
independiente si influyen en ese ajuste.
