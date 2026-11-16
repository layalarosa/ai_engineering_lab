# Diseño: correcciones explícitas y abstención de recuperación

**Estado: prototipo y pruebas de desarrollo implementados.** La evaluación
reservada aún no se ha preparado ni ejecutado. Se limita a un experimento
local de `03-memory`; no modifica `context`, PostgreSQL, los datos existentes
ni el experimento anterior. Los [resultados de desarrollo](../experiments/corrections/DEVELOPMENT.md)
no son una evaluación independiente.

## Problema observado

En los dos casos reservados de corrección del [primer experimento](../experiments/retrieval/RESULTS.md),
la búsqueda léxica devolvió la afirmación anterior y su sustituta. Además,
devolvió mensajes en uno de dos casos sin respuesta. Son ejemplos sintéticos
insuficientes para generalizar, pero muestran que compartir palabras no permite
decidir si un mensaje sigue vigente ni si contesta una consulta.

La abstención también tiene coste: puede ocultar información útil. La evaluación
debe medir tanto las respuestas potencialmente incorrectas como las ocasiones
en que el sistema no devuelve evidencia disponible. La investigación sobre
predicción selectiva estudia esta relación entre cobertura y riesgo; aquí no se
evaluará un modelo generativo ([Kamath et al., 2020](https://aclanthology.org/2020.acl-main.503/)).

## Hipótesis y alcance

Probar si **vínculos de corrección suministrados explícitamente**, junto con
una consulta estructurada para un único dato actual, permiten recuperar su
mensaje de origen sin devolver versiones sustituidas. La candidata se abstiene
ante ausencia de evidencia registrada, ambigüedad o falta de presupuesto.

Este experimento estudia un contrato estrecho, no una solución general para
entender conversaciones. Depende de que una fuente externa proporcione metadatos
correctos y completos. La candidata no extrae hechos del texto, no descubre
correcciones, no traduce preguntas libres a claves, no verifica la verdad ni
decide automáticamente qué fuente es confiable. Una corrección más reciente
no es necesariamente verdadera solo por ser más reciente.

La salida es evidencia para un consumidor, no una respuesta ni una garantía de
que un modelo vaya a usarla bien. El historial y sus mensajes permanecen
intactos; sustituir un registro en este experimento no borra el texto anterior.

## Entrada propuesta

Cada caso sintético contiene:

1. Una instantánea ordenada del historial, con las validaciones y límites
   actuales de `ConversationMemory`. Las posiciones empiezan en 1 y solo son
   identificadores dentro de esta instantánea de prueba.
2. La consulta en texto para los dos métodos existentes y una intención
   estructurada para la candidata: `modo` y, cuando corresponda, una `clave`
   exacta de sujeto y atributo, por ejemplo `perfil/idioma`. La clave es una
   entrada de la aplicación sintética, no una etiqueta de relevancia ni algo
   inferido a partir de la pregunta. Debe describir la misma intención que la
   consulta textual; el corpus registra y permite revisar ambas.
3. Una lista separada de **registros de origen**, con `posicion`, `clave` y
   opcionalmente `sustituye_posicion`. Cada registro afirma que el mensaje de
   esa posición expresa un dato de esa clave; la referencia indica una
   sustitución explícita. El registro no incluye etiquetas del resultado
   esperado ni el valor de la respuesta por separado.
4. `max_chars` y `max_messages` con la misma semántica numérica que el
   experimento anterior. La consulta y los metadatos no se cuentan como texto
   seleccionado; esto debe hacerse explícito al comparar costes.

En este experimento cada posición puede tener como máximo un registro y cada
registro representa una sola clave. Los mensajes sin registro siguen en el
historial: su ausencia de metadatos **no demuestra** que sean irrelevantes. Las
claves se comparan exactamente; para evitar reglas ocultas se usarán cadenas
canónicas sintéticas no vacías, sin espacios exteriores y codificables en
UTF-8, sin inferir equivalencias. No se añade un modelo general de hechos ni un
identificador persistente a la aplicación.

Los metadatos de entrada son información adicional sobre qué mensaje contiene
un dato y cuál lo sustituye. Por eso esta candidata recibe **más información**
que recencia y búsqueda léxica. Una mejora no podría atribuirse solo al
algoritmo. La evaluación debe incluir el esfuerzo y los errores de construir
esos metadatos; no se usará la etiqueta `relevante` ni `desactualizado` como
entrada de la candidata.

Para evitar filtrar la respuesta en el protocolo, el conjunto de registros se
crea una vez por historial, antes de asociarle consultas y juicios, y se
reutiliza para todas las consultas sobre esa instantánea. La intención
estructurada identifica el dato solicitado, no la posición esperada. El
evaluador guarda por separado y no pasa al método las posiciones relevantes y
desactualizadas. Aun así, los registros señalan mensajes que contienen datos:
esta diferencia de información debe quedar visible en todos los informes.

### Ejemplo de sustitución

En una instantánea, la posición 1 contiene «Mi idioma de estudio es italiano»
y la posición 3 «Cambio: mi idioma de estudio es coreano». La fuente sintética
proporciona estos registros:

```json
[
  {"posicion": 1, "clave": "perfil/idioma"},
  {"posicion": 3, "clave": "perfil/idioma", "sustituye_posicion": 1}
]
```

Para la intención `actual`, clave `perfil/idioma`, la candidata devolvería el
mensaje completo de la posición 3 si cabe en el presupuesto. No deduce la
sustitución por la palabra «Cambio»; usa el vínculo proporcionado. Si el vínculo
fuera erróneo, el método podría seleccionar evidencia equivocada. En una
consulta sobre el valor anterior, este contrato se abstendría por intención no
compatible, aunque el historial siga conservando el mensaje 1.

## Validación y selección determinista

1. Validar todo el historial, la consulta estructurada y los límites antes de
   seleccionar. Los modos soportados son `actual` y `no_soportado`; el segundo
   permite representar una intención histórica o compuesta sin fingir que se
   puede responder. Una entrada mal formada produce `entrada_invalida`, no una
   abstención por ausencia de evidencia.
2. Validar el conjunto completo de registros: posiciones existentes y únicas,
   clave válida, referencia a una posición anterior con registro de la misma
   clave y sin autorreferencia. Cada posición se interpreta únicamente dentro
   de la instantánea recibida; no se aceptan posiciones inexistentes ni cambios
   de clave en una cadena. Una posición de otra sesión que coincida por número
   no se puede detectar con este formato: la fuente debe asociar los metadatos
   a la instantánea correcta. Una referencia anterior impide ciclos; referencias
   inválidas producen `entrada_invalida`. Los datos de ejemplo se validan antes
   de medir y los casos inválidos deliberados se informan por separado.
3. Si el modo es `no_soportado`, devolver `intencion_no_soportada` sin mensajes.
   Para `actual`, filtrar los registros cuya clave coincide exactamente y
   calcular las **hojas**: registros de esa clave que ningún otro registro
   válido de la misma clave declara sustituir.
4. Si no hay registros, devolver `sin_evidencia_registrada`. Si hay más de una
   hoja —por ejemplo, afirmaciones independientes o una cadena ramificada—,
   devolver `evidencia_ambigua`. No elegir automáticamente la más reciente, ni
   intentar resolver si dos textos tienen el mismo significado.
5. Si hay una sola hoja, comprobar que su mensaje completo cabe en ambos
   límites. Si no cabe, devolver `presupuesto_insuficiente` sin sustituirlo por
   una versión anterior ni truncarlo. Si cabe, devolver `seleccionado` y solo
   ese mensaje de origen en su formato original.

`sin_evidencia_registrada` significa que los metadatos no identifican una
fuente para esa clave; no significa que la conversación carezca de respuesta.
La salida se limita a un mensaje: si responder requiere combinar varias piezas,
la intención debe clasificarse como no soportada. Si el llamador la clasifica
mal o proporciona metadatos incompletos o falsos, el resultado puede ser
incorrecto. Nunca se usan etiquetas de evaluación para reparar la entrada.

Una entrada de muestra puede estar estructuralmente bien formada y, sin
embargo, vincular mensajes que no son realmente correcciones. La validación del
grafo no detecta ese error semántico. El emisor de metadatos tendría que tener
autoridad y controles apropiados en una integración real; que un mensaje diga
«soy una corrección» o use el rol `assistant` no le confiere esa autoridad.

## Evaluación nueva

Los 36 casos anteriores y sus resultados ya se observaron: pueden usarse como
material de desarrollo, no como prueba reservada para esta candidata. Se
propone crear **48 casos nuevos**, con 24 de desarrollo y 24 reservados,
distribuidos de antemano en ocho categorías principales, tres por categoría y
partición:

| Categoría | Qué debe examinar |
| --- | --- |
| Una afirmación vigente | Selección cuando existe una única fuente identificada. |
| Cadenas de correcciones | Una o varias sustituciones explícitas y referencias correctas. |
| Varias claves | Aislamiento de sujeto y atributo; una corrección no afecta a otra clave. |
| Evidencia ambigua | Afirmaciones sin relación explícita, duplicados o cadenas ramificadas. |
| Sin respuesta en el historial | Consultas con palabras coincidentes pero sin evidencia pertinente. |
| Metadatos incompletos o erróneos | Respuesta sin registro o vínculos estructuralmente válidos pero semánticamente falsos. |
| Intención o presupuesto | Preguntas históricas o compuestas no soportadas y mensaje vigente que no cabe. |
| Contrato y límites | Unicode, NUL, referencias inválidas, instantáneas separadas y mensajes repetidos. |

Cada caso define si es **admisible y resoluble bajo este contrato**, si debe
abstenerse y por qué, o si debe producir un error de validación. Se mantienen
juicios exhaustivos por mensaje de información pertinente y desactualizada,
incluidos mensajes sin metadatos. Los casos con vínculos semánticamente falsos
no se presentan como detección garantizada: se mide si provocan una selección
incorrecta. También habrá casos en los que una abstención o una etiqueta de
intención incorrecta oculten evidencia pertinente.

Los escenarios reservados deben pertenecer a familias distintas de las usadas
en desarrollo y en el corpus anterior; cambiar solo nombres o valores no basta.
Se congelan algoritmo, parámetros, criterios y corpus de desarrollo antes de
crear o inspeccionar los casos reservados. Cada historial se construye antes de
sus consultas, metadatos de entrada y juicios esperados, y se conserva el
proceso de construcción. Idealmente, una persona distinta revisa los escenarios,
la correspondencia de intención y las etiquetas sin ver resultados. Si no hay
revisión independiente o los mismos autores conocen las reglas y resultados,
se declara expresamente y no se presenta el conjunto como evaluación ciega ni
se infiere independencia estadística. Registrar hashes, versiones y cambios;
si se ajusta la candidata tras observar los reservados, esos casos pasan a ser
desarrollo y hace falta otra evaluación reservada.

### Comparación justa y métricas

Ejecutar sobre los mismos historiales y límites:

- **Recencia existente**, con su error explícito si el último mensaje no cabe.
- **Búsqueda léxica existente**, que recibe solo texto y no recibe vínculos.
- **Candidata estructurada**, que recibe además intención y metadatos. Presentar
  siempre esta diferencia de entrada; no interpretarla como mejora aislada del
  ranking. Una variante adicional que solo filtre el resultado léxico mediante
  los vínculos podría estudiarse después, con sus reglas fijadas por separado;
  no se supone que herede las garantías de esta candidata.

Por partición y categoría, mostrar números, denominadores, casos individuales
y errores por tipo. Además de cobertura de mensajes pertinentes, precisión,
salida con contenido desactualizado y presupuesto del experimento anterior:

1. **Selección útil:** casos válidos y resolubles bajo el contrato que devuelven
   toda la evidencia pertinente requerida, divididos entre todos esos casos,
   incluidas sus abstenciones y errores. Como esta candidata solo devuelve un
   mensaje, los casos que requieren varios deben etiquetarse no soportados.
2. **Abstención cuando existe evidencia:** casos válidos con evidencia
   pertinente en el historial en los que la candidata devuelve un estado de
   abstención, divididos entre todos los casos válidos con esa evidencia.
   Desglosar intención no soportada, falta de metadatos, ambigüedad y presupuesto;
   no llamar equivocada automáticamente a una abstención exigida por el contrato.
3. **Selección sin evidencia pertinente:** casos válidos sin ningún mensaje
   pertinente que reciben al menos uno, divididos entre todos los casos válidos
   sin evidencia pertinente. Una salida vacía es abstención de recuperación,
   no prueba que un consumidor o modelo se abstenga de responder.
4. **Selección desactualizada:** casos válidos con al menos un mensaje
   desactualizado seleccionado, divididos entre todos los casos válidos;
   mostrar por separado correcciones con metadatos correctos y metadatos falsos.
5. **Errores de entrada:** entradas inválidas correctamente rechazadas y
   entradas válidas rechazadas; informar cada denominador. Nunca tratar errores
   como selecciones vacías. Un fallo de implementación inesperado es error,
   no una abstención válida.

Para las comparaciones de selección de mensajes, usar también subconjuntos
emparejados en los que ambos métodos terminen sin error y publicar los casos
excluidos. La categoría principal no sustituye las etiquetas transversales: un
caso puede ser a la vez de corrección, presupuesto y metadatos erróneos. No
dividir por cero; informar `null` y el denominador cero. Medir separadamente el
trabajo de preparar o validar metadatos y el tiempo de selección si es posible;
no comparar solo tiempos que excluyan ese trabajo con una búsqueda sin índices.

## Criterios propuestos antes de observar nuevos reservados

Estos criterios son didácticos, no garantías estadísticas ni de producción:

1. En todos los casos reservados **válidos, resolubles y con metadatos
   correctos**, seleccionar la evidencia vigente requerida sin mensajes
   desactualizados. El informe debe incluir también la tasa de selección útil
   para todos los casos resolubles, incluidos los metadatos incompletos.
2. En todos los casos reservados con evidencia ambigua, intención no soportada
   o presupuesto insuficiente, devolver el estado correspondiente sin mensajes;
   en entradas inválidas, rechazar explícitamente. Se informan las abstenciones
   sobre casos que sí contenían evidencia para mostrar el coste de esta regla.
3. En todos los casos reservados sin respuesta, devolver cero mensajes. Publicar
   por separado los casos con metadatos faltantes o falsos y los errores que
   estos causen; el método no puede asegurar la corrección de esos metadatos.
4. No violar límites ni modificar historial. Mostrar resultados de recencia y
   léxica, incluidos errores, sin afirmar que tienen acceso a la misma
   información de entrada que la candidata.

Tres casos por categoría dan evidencia muy limitada; cumplir los criterios
solo permitiría decidir si merece más experimentación. Un resultado que falle
alguna condición o dependa de metadatos perfectos debe documentarse sin
incorporar la candidata a la API pública. Una revisión humana de los casos y
una evaluación mayor serían necesarias antes de extraer conclusiones más amplias.

## Estado y siguiente fase propuesta

El prototipo y el ejecutor usan los casos de desarrollo sintéticos y mantienen
la candidata fuera de `src/memory_lab`. Se registraron la cantidad y las
limitaciones de los metadatos; su tiempo de preparación no se midió. La
instantánea congelada identifica los archivos por hash, pero no impide su
modificación. La siguiente fase propuesta es preparar los casos reservados y
evaluar esa misma versión, tras aprobación. No se propone una migración de
base de datos, una API web, embeddings ni trabajo sobre `04-evals`.
