# Diseño: evaluación de recuperación de memoria

**Estado: experimento local implementado.** El objetivo es decidir si recuperar
mensajes según una consulta aporta algo frente a la ventana reciente existente.
La candidata solo existe en el experimento: no cambia `context`, el esquema de
PostgreSQL ni añade dependencias. Los [resultados iniciales](../experiments/retrieval/RESULTS.md)
son didácticos y no cumplen todos los criterios de adopción propuestos.

## Qué se quiere aprender

La recencia entrega un sufijo contiguo del historial, incluso si los últimos
mensajes no responden a la consulta. La recuperación léxica propuesta busca
palabras compartidas y puede seleccionar mensajes antiguos no consecutivos.
Ambas usan el mismo historial y presupuesto, pero tienen limitaciones distintas:
las palabras compartidas no garantizan relevancia, y una preferencia antigua
puede dejar de ser válida después de una corrección.

Ejemplo ilustrativo, con presupuesto para dos mensajes y caracteres suficientes:

| Posición | Mensaje del historial | Juicio para la consulta |
| --- | --- | --- |
| 1 | «Prefiero usar Python». | Desactualizado: hay una corrección posterior. |
| 2 | «Entendido». | No pertinente. |
| 3 | «Ahora prefiero usar C#». | Pertinente y vigente. |
| 4 | «Hoy hace calor». | No pertinente. |
| 5 | «De acuerdo». | No pertinente. |

Consulta externa: «¿Qué lenguaje prefiero usar ahora?». La recencia entrega 4 y
5. La estrategia léxica definida abajo encuentra coincidencias en 3 y 1 y
podría devolver ambas. Así, encontrar
el mensaje correcto no impide entregar también uno desactualizado. Esta
ilustración no es una medición ni prueba que un mensaje posterior siempre sea
más verdadero. No se implementa extracción automática de hechos.

## Unidad de evaluación y etiquetas

Un caso contiene un historial sintético ordenado, una consulta externa al
historial, `max_messages`, `max_chars` y juicios por posición de mensaje. La
posición identifica un mensaje solo dentro de ese historial de prueba: no es
un identificador estable de producción y puede reutilizarse tras `clear`.
Se usan roles `user` y `assistant`, pero el contenido de ninguno adquiere
autoridad por mencionar una instrucción o declararse verdadero.

La evaluación requiere revisar **todos** los mensajes de cada historial para
esa consulta y clasificarlos de forma excluyente. El corpus inicial fue generado
y etiquetado por el asistente, sin revisión humana independiente; sus juicios
son provisionales y deben revisarse antes de extraer conclusiones más amplias:

- `relevante`: aporta información necesaria o útil para contestar la consulta
  conforme a los hechos expresados en el escenario.
- `desactualizado`: contradice una corrección explícita aplicable a la consulta
  y no se necesita para contestarla. No se asigna esta etiqueta solo por ser
  antiguo o por discrepar de otro mensaje sin una corrección clara.
- `otro`: no es relevante ni desactualizado para esa consulta.

Puede haber varios mensajes relevantes o ninguno. Las etiquetas describen el
escenario sintético, no garantizan la verdad de los mensajes. Si una consulta
pide explícitamente el historial de cambios, una preferencia anterior podría
ser relevante para esa consulta; no se marca automáticamente como obsoleta.
Se guardan además una breve justificación y la categoría del caso para revisar
desacuerdos. Datos o instrucciones dentro del historial se tratan como datos;
el experimento no sigue órdenes contenidas en ellos.

## Corpus propuesto

Crear inicialmente **36 casos sintéticos**, sin datos personales ni secretos:
24 de desarrollo y 12 reservados. Se proponen seis categorías con cuatro casos
de desarrollo y dos reservados por categoría:

| Categoría | Qué permite observar |
| --- | --- |
| Reciente pertinente | Si el método conserva la ventaja de una ventana reciente. |
| Pertinente antiguo entre distracciones | Si encuentra información fuera de la ventana reciente. |
| Corrección explícita | Si recupera la versión vigente y también muestra la antigua. |
| Variación de palabras | Qué ocurre con paráfrasis, acentos o términos relacionados sin coincidencia exacta. |
| Sin respuesta | Si devuelve mensajes pese a no existir contenido pertinente. |
| Presupuesto y entradas límite | Mensajes largos, duplicados, Unicode o NUL y restricciones de selección. |

Los casos reservados deben usar **historias y familias de escenarios diferentes**
a los de desarrollo, no una reformulación de estos. Las categorías son el eje
principal; un caso también puede registrar otras características. Las etiquetas
se revisan y las particiones se fijan antes de ajustar el método. Si se ajusta
el método después de mirar los resultados reservados, ese conjunto deja de ser
una prueba independiente y se necesitará otro. La muestra pequeña es didáctica:
no representa usuarios reales ni permite afirmar una mejora general.

Los casos deben respetar el contrato actual de mensajes y límites de contexto.
Incluir al menos un caso con varios relevantes y otro con mensaje más reciente
demasiado largo. Cada etiqueta se registra para todos los mensajes, incluidos
los no seleccionados; asumir que lo no juzgado es irrelevante sesgaría las
métricas. Una revisión de NIST muestra que los juicios incompletos pueden
afectar la evaluación de recuperación ([NIST](https://www.nist.gov/publications/retrieval-evaluation-incomplete-information)).

## Métodos y presupuesto compartido

1. **Referencia por recencia.** Ejecutar `context(max_chars, max_messages)` tal
   como existe: sin modificar el orden, la parada en el primer mensaje que no
   cabe ni el error si el último es demasiado largo. No recibe la consulta.
2. **Candidata léxica propuesta.** Para consulta y contenido, aplicar NFKC y
   `casefold` a copias para comparar, sin modificar el texto original. Separar
   secuencias de letras o números Unicode mediante `[^\W_]+` en una expresión
   regular Unicode; usar conjuntos de términos,
   sin stemming ni lista de palabras vacías. Su puntuación es el número de
   términos distintos compartidos dividido entre el número de términos distintos
   de la consulta. La consulta debe tener de 1 a 4.000 puntos de código y ser
   codificable en UTF-8; si no contiene términos, devuelve lista vacía.
   Solo considera puntuación mayor que cero; ordena de mayor a menor puntuación
   y, en empate, primero la posición más reciente.
3. La candidata recorre ese ranking, añade mensajes completos que quepan en
   ambos límites y salta los que no quepan; se detiene al agotar candidatos o
   llegar a `max_messages`. Devuelve los seleccionados en orden cronológico,
   sin alterar sus roles ni contenido. Si no hay candidatos o ninguno cabe,
   devuelve lista vacía. La consulta no se añade al historial ni al presupuesto.

La expresión regular es una regla simple, no un segmentador lingüístico:
puede tratar de forma deficiente algunos idiomas o signos como `#` en `C#`.
La normalización solo afecta a la comparación; puntos de código del contenido
original determinan `max_chars`, igual que en la implementación actual. El
esquema `bytea` conserva NUL y texto original; para el experimento inicial se
leerá un historial completo sintético en proceso, sin índice SQL ni cambios de
esquema. En un producto habría que definir una lectura consistente, límites y
autorización antes de buscar sobre sesiones reales.

La comparación aplica las mismas cotas numéricas, pero **no la misma política
de empaquetado**: recencia conserva un sufijo y la candidata salta mensajes que
no caben. Esa diferencia debe mostrarse al interpretar resultados. Un error de
la referencia se registra como error, no como respuesta vacía ni como cero
silencioso. No se cambia su contrato para facilitar el experimento. La consulta
y las etiquetas son sintéticas y nunca deben escribirse en registros de
aplicación con información real.

## Medición

Para cada caso sin error, sea `S` el conjunto de posiciones devueltas, `R` el
conjunto etiquetado relevante y `D` el desactualizado. Las posiciones, no el
texto, distinguen mensajes duplicados. Se informarán métricas por categoría y
en total, además de los resultados individuales:

| Medida | Definición y alcance |
| --- | --- |
| Cobertura de relevantes | `|S ∩ R| / |R|`, solo si `R` no está vacío. Media por caso, no por mensaje. |
| Precisión de mensajes seleccionados | `|S ∩ R| / |S|`, solo si `S` no está vacío. Informar cuántos casos se excluyen por salida vacía. |
| Casos completos | Fracción de casos con `R` no vacío en los que `R` está contenido en `S`. |
| Devolución desactualizada | Fracción de casos con al menos un mensaje de `D` en `S`; informar también el subconjunto de correcciones. |
| Salida en casos sin respuesta | Fracción de casos con `R` vacío en los que `S` no está vacío. También informar cuántos devuelven vacío. |
| Errores | Número y tipo por método; desglosar el caso en que el último mensaje supera el presupuesto. |
| Coste observado | Cantidad de mensajes y caracteres seleccionados, y tiempos medidos de selección p50/p95 con entorno y repeticiones documentados. |

La media de cobertura y los casos completos excluyen explícitamente los errores;
se muestran sus denominadores y la tasa de error junto a ellos para impedir que
fallar mejore artificialmente el resultado. Para la diferencia entre métodos se
usa el mismo subconjunto de casos positivos en que ambos terminan sin error y se
informa por separado cualquier caso excluido. La salida vacía no cuenta como
error y, si hay relevantes, aporta cobertura cero. En los casos sin respuesta,
devolver vacío es solo una abstención de recuperación: no demuestra que una
aplicación o modelo vaya a abstenerse de contestar. La recencia no tiene señal
para abstenerse según la consulta, por lo que puede salir perjudicada en esa
medida; se reporta como parte de la comparación, no como fallo contractual.

La selección se evalúa como conjunto; el orden cronológico de salida no es un
ranking de relevancia. Tampoco se evalúa generación, corrección de respuestas,
tokens, latencia de red o calidad de un modelo. Recuperar un mensaje relevante
no garantiza que un modelo lo use bien; algunos modelos pueden ser sensibles a
la posición del contenido en el contexto ([Liu et al.](https://arxiv.org/abs/2307.03172)).

## Criterios de decisión propuestos

Fijar estos criterios antes de ver resultados reservados; son umbrales de
trabajo del laboratorio, no garantías estadísticas:

1. La candidata mejora la cobertura media en al menos **0,20 puntos** frente a
   recencia en los casos reservados con relevantes, mostrando denominadores,
   errores y comparación por caso.
2. En los casos reservados de corrección, recupera todos los mensajes vigentes
   relevantes de cada caso y no devuelve ninguno etiquetado desactualizado.
3. En los casos reservados sin respuesta, devuelve vacío en todos ellos. Se
   informa por separado el comportamiento de la referencia.
4. No aparecen errores nuevos ni se exceden los límites. Los tiempos y casos
   límite se revisan manualmente antes de decidir si el coste es aceptable.

Superar estas condiciones solo justificaría seguir experimentando en el
laboratorio; con dos casos reservados por categoría no demuestra robustez ni
preparación para producción. Si no se cumplen, documentar los casos y conservar
recencia; se podrá proponer otra estrategia y un nuevo conjunto reservado. La
candidata léxica deliberadamente simple puede fallar en correcciones o
paráfrasis: ese resultado sería información útil, no una razón para ocultar el
fallo. Embeddings o pgvector se considerarían después de observar necesidades
concretas, con evaluación y conjunto reservado adecuados.

## Implementación del experimento

El corpus y ejecutor están en `experiments/retrieval`. La candidata léxica
solo existe allí y las pruebas comprueban corpus, límites, errores y fórmulas
con resultados conocidos. El informe incluye posiciones y etiquetas sintéticas,
no el texto de los mensajes. No se añade un framework de evaluación ni se
desarrolla el proyecto independiente `04-evals` en esta fase.
