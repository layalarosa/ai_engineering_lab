# Diseño: procedencia de afirmaciones y correcciones

**Estado: demostración en proceso implementada; integración pendiente.**
Propone cómo una aplicación podría crear e identificar evidencia estructurada
para un único dato de una sesión. La [demostración](../experiments/provenance/README.md)
usa identidad y política simuladas; no modifica la API, el esquema PostgreSQL,
el prototipo congelado ni sus datos.

## El problema que queda abierto

La [evaluación del prototipo](../experiments/corrections/HELDOUT.md) mostró que
un vínculo bien formado puede ser falso: la candidata confió en un registro que
atribuía una fecha inexistente a un mensaje. Verificar posiciones y cadenas no
demuestra que la información sea cierta ni que quien la proporciona tenga
autoridad para hacerlo.

La procedencia responde a preguntas como **quién afirmó algo, mediante qué
operación y en qué mensaje quedó registrado**. El modelo [PROV de W3C](https://www.w3.org/TR/prov-dm/)
ofrece conceptos útiles para pensar en entidades, actividades y agentes; aquí
no se propone implementar ese estándar ni agregar una biblioteca para él.
Incluso una procedencia auténtica solo acredita el origen de una afirmación,
no que el hecho afirmado sea verdadero.

## Caso de uso y alcance

Una aplicación de ejemplo permite que una persona elija explícitamente una
preferencia sencilla, como `perfil/idioma_preferido`, de una lista de campos
soportados. La aplicación muestra el valor que va a guardar y, al confirmarlo,
envía una operación estructurada. Memory guarda un mensaje generado de forma
determinista a partir de esa operación y una referencia de procedencia en la
misma transacción. Si después se cambia el valor, la persona confirma también
qué versión sustituye.

Este camino solo cubre un **campo actual y de valor único** por sesión. Las
conversaciones libres, afirmaciones importadas, preguntas históricas y datos
que requieren múltiples fuentes quedan fuera. No se infiere un hecho desde el
texto de un LLM, un documento o un mensaje recibido: esos contenidos pueden
contener instrucciones o afirmaciones no verificadas. [OWASP](https://genai.owasp.org/glossary/prompt-injection/)
describe el riesgo de instrucciones introducidas a través de contenido externo.

La aplicación debe identificar y autorizar a quien confirma la operación. El
ejemplo de laboratorio podrá simular ese límite, pero una identidad sintética o
un campo `actor` suministrado por el cliente **no implementan autenticación**.
El `session_id`, un rol `user` o `assistant` en un mensaje, un hash y el texto
«confirmado» tampoco son credenciales. El usuario podría confirmar un valor
equivocado o una interfaz comprometida podría engañarlo: el diseño no convierte
la confirmación en una garantía de verdad.

## Decisión: crear evidencia, no certificar texto arbitrario

| Alternativa | Consecuencia |
| --- | --- |
| Vincular una clave a cualquier mensaje existente porque coinciden palabras. | No verifica que el mensaje contenga el valor y reproduce el fallo observado. |
| Pedir que un modelo extraiga y certifique automáticamente el dato. | Su error podría convertirse en una corrección con apariencia de autoridad. |
| **Crear un mensaje estructurado junto con la afirmación confirmada.** | Hace explícita la fuente y liga el dato guardado al mensaje generado; exige una operación y una interfaz especializadas. |

Se propone la tercera alternativa para el laboratorio. La aplicación envía
clave y valor como campos tipados; el servicio valida la clave contra una lista
pequeña y aplica reglas por tipo. A partir del valor validado genera el mensaje
canónico, en vez de aceptar otro texto independiente como prueba. Antes de
confirmar, la interfaz muestra ese mismo contenido o una representación fiel.
La representación generada también debe cumplir el límite actual de 4.000
puntos de código; si no cabe, se rechaza antes de escribir. No se modifica ni
reescribe ningún mensaje histórico.

Este vínculo evita que el servicio acepte, por esta vía, una clave asociada a
un texto elegido independientemente que no exprese el dato. No impide que la
clave elegida, el valor confirmado o el adaptador autorizado sean incorrectos.
La aplicación debe conservar la distinción entre «afirmado por esta persona»
y «hecho verificado». En particular, no se añadirán automáticamente registros
de procedencia a mensajes ya guardados: carecen de la confirmación descrita.

## Información mínima propuesta

Los nombres son conceptuales; no son columnas ni una API implementadas:

| Dato | Origen y función |
| --- | --- |
| Identidad de sesión y **generación del historial** | El servicio identifica la sesión y una generación nueva tras creación o vaciado. Evita confundir un historial con otro que reutilice el mismo UUID o posiciones. |
| `message_id` | Identificador opaco generado por el servicio para el mensaje canónico; se refiere a esa generación. No es una credencial. |
| `statement_id`, clave y valor validado | Identifican la afirmación y el dato confirmado. El valor sigue siendo información potencialmente sensible. |
| `supersedes_statement_id` | Opcional en la primera afirmación; obligatorio para una corrección, apunta a la versión vigente de la misma clave y generación. |
| Identidad del actor y ámbito | Proceden de un adaptador de autenticación y una decisión de autorización del servidor, no del cuerpo del mensaje. En el laboratorio serían simulados. |
| `operation_id` | Identificador elegido para reconocer un reintento de la misma operación dentro de su ámbito y generación. |
| Hora de aceptación y versión de política | Se asignan en el servidor para explicar la decisión. La hora no prueba cuándo el dato se volvió verdadero. |

La representación exacta del actor, las claves soportadas, el tipo de cada valor
y la política de autorización requieren un consumidor concreto. No se propone
un sistema genérico de identidades, una jerarquía de roles ni multiinquilino en
esta fase. El actor se deriva del contexto autenticado; ni una clave válida ni
un `operation_id` autorizan acceso a una sesión.

### Por qué hacen falta identificadores nuevos

Hoy PostgreSQL usa `(session_id, position)`. Tras `clear()`, la posición vuelve
a empezar en 1; tras `delete()`, el mismo UUID puede volver a crearse. Una
referencia que conserve solo esos dos valores podría apuntar a un mensaje
distinto. La persistencia actual tampoco tiene `message_id` ni generación.

Antes de integrar esta propuesta habría que diseñar una migración versionada y
compatible, o una base experimental separada, para introducir identificadores
de mensaje y generación. No se asumirán identificadores estables para datos
existentes ni se reconstruirá su procedencia retroactivamente. Una operación
antigua debe incluir la generación que conocía y rechazarse si `clear` o
`delete` la invalidaron, incluso si otra sesión reutilizó el mismo UUID.

## Operaciones propuestas

El contrato es deliberadamente pequeño y está expresado como operaciones
conceptuales, no como endpoints ya disponibles:

1. **Confirmar primera afirmación.** Recibe generación, clave permitida, valor
   validable y `operation_id`. La identidad procede del contexto del servidor.
   Si ya existe una afirmación vigente para esa clave, devuelve conflicto y no
   crea una segunda raíz silenciosa.
2. **Confirmar corrección.** Recibe lo mismo y el `statement_id` que la persona
   vio y desea sustituir. Comprueba que pertenece a la misma generación y clave,
   y que todavía es la versión vigente. Si no, devuelve conflicto para que el
   consumidor vuelva a consultar; no elige automáticamente la más reciente.
3. **Consultar evidencia actual.** Tras comprobar autorización de lectura,
   devuelve el mensaje canónico y su procedencia aceptada para una clave, o un
   estado explícito si no existe o hay una inconsistencia. La lectura debe
   obtener mensaje y metadatos de una misma instantánea. No se devuelve como
   verdad verificada ni se ejecutan instrucciones contenidas en el valor.

Ejemplo conceptual de corrección: la persona vio la afirmación `A` de
`perfil/idioma_preferido` y confirma cambiar el valor a `español`. El servidor
crearía el mensaje canónico, la afirmación `B` y el vínculo `B → A`. Si otra
operación ya sustituyó `A`, el cambio basado en `A` falla con conflicto. El
mensaje anterior se conserva hasta que una operación de eliminación aplicable
lo borre; no se declara globalmente falso ni se elimina por ser antiguo.

## Confianza, transacciones y fallos

Para una futura implementación persistente, cada escritura tendría esta
secuencia dentro de una transacción, suponiendo que **todos los escritores**
respetan el protocolo:

1. El adaptador autenticado determina el actor. El servicio comprueba el acceso
   al ámbito y a la generación actual antes de revelar si existe una operación
   previa. Si no puede verificar autorización, rechaza la operación.
2. Adquiere el bloqueo de la sesión y comprueba la generación. Busca una
   operación previa por generación, actor autenticado y `operation_id`: si es
   una repetición exacta devuelve el resultado registrado; si reutiliza el ID
   con otra solicitud devuelve conflicto. Esta comprobación precede a la
   condición de versión vigente, pues una corrección ya confirmada habrá
   sustituido por definición a su predecesora.
3. Para una operación nueva, comprueba límites de mensajes, clave y valor, y
   relee la versión vigente. Una corrección cuyo predecesor ya no esté vigente
   falla sin insertar mensaje ni registro.
4. Si todo es válido, inserta en la misma transacción el mensaje canónico, la
   afirmación, el vínculo y el registro de operación. Confirma o revierte todo;
   no se publica una referencia a un mensaje que no se haya guardado.

PostgreSQL mantiene los bloqueos de fila hasta terminar la transacción
([documentación de PostgreSQL 16](https://www.postgresql.org/docs/16/explicit-locking.html#LOCKING-ROWS)).
El bloqueo de la sesión ofrece una opción sencilla para el límite actual de
1.000 mensajes, no una garantía si otro escritor evita ese protocolo. Un
adaptador de identidad o autorización externo puede cambiar durante la
operación: habría que definir cómo se comprueba y con qué vigencia; un bloqueo
de PostgreSQL por sí solo no sincroniza políticas de un servicio externo.

Para dos correcciones concurrentes que apuntan a `A`, la primera confirmada
crea `B`; la segunda, al obtener el bloqueo, ve que `A` ya no es vigente y
devuelve conflicto. Una actualización de otra clave no debe quedar sustituida
por accidente, aunque pueda esperar el mismo bloqueo de sesión.

### Reintentos y borrado

Si se pierde la respuesta después del commit, el consumidor no debe generar
otro `operation_id` y repetir sin comprobar. Se propone que un reintento con el
mismo actor autorizado, generación, `operation_id` y contenido canónico
idéntico devuelva el resultado ya registrado; el mismo identificador con otra
operación devuelve conflicto. La igualdad compara la solicitud estable
canonizada, incluido el predecesor cuando corresponda, no los IDs ni la hora
generados por el servidor en la primera ejecución. La comparación debe ser
exacta y acotada, sin registrar el valor en logs. Un identificador aleatorio no
sustituye la autorización y no se promete *exactly once* fuera de este contrato.

La deduplicación solo se garantiza mientras exista su registro y la generación
siga activa. `clear` cambia la generación y elimina los mensajes y registros
activos asociados; una petición tardía de la generación anterior se rechaza.
`delete` elimina la sesión y sus datos activos; si se crea otra con el mismo
UUID, recibe una generación nueva. Hay que fijar explícitamente retención y
acceso de los registros de operación antes de implementarlos. No se promete
que una repetición funcione después de vaciar o eliminar, ni que esos borrados
eliminen inmediatamente copias de seguridad o datos ya leídos por un consumidor.

Las lecturas que empezaron antes de un `clear` o `delete` podrían terminar
después con su instantánea previa, igual que en el diseño PostgreSQL actual. Si
una aplicación necesita revocar de forma inmediata evidencia ya entregada,
necesita un contrato adicional; cambiar la generación no retira copias que ya
salieron del servicio.

## Matriz de decisiones

| Situación | Decisión propuesta |
| --- | --- |
| Fuente autorizada confirma un valor válido y se crea su mensaje canónico. | Registrar como **afirmación atribuida**, no como verdad verificada. |
| Mensaje libre, salida de modelo o texto externo solicita crear metadatos. | No promoverlo automáticamente; requiere el flujo explícito autorizado. |
| El cliente envía un campo de identidad de actor o rol para esta operación. | Rechazar como campo no admitido; la identidad se deriva del contexto confiable. |
| Corrección de otra clave, otra generación o una versión ya sustituida. | Rechazar sin cambios; distinguir conflicto de entrada inválida según el contrato futuro. |
| Dos afirmaciones vigentes incompatibles por datos externos o corrupción. | No seleccionar una silenciosamente; informar inconsistencia o abstenerse. |
| Se autoriza registrar, pero se confirma un valor falso. | La procedencia seguirá siendo atribuible; no demuestra la verdad del valor. |
| No puede comprobarse el acceso a la sesión o la política vigente. | Rechazar el acceso; no filtrar contenido ni existencia de operaciones. |

En logs operativos solo se proponen estado, tipo de operación, duración y
errores saneados. No se registran el valor, el texto ni credenciales. Los
identificadores y metadatos de actor también pueden ser sensibles; su retención,
acceso y correlación requieren una política definida para el entorno real.

## Cómo comprobar la propuesta

Antes de considerar una integración con PostgreSQL, un experimento aislado
podría usar una identidad y política **simuladas**, sin presentarlas como
seguridad real. Las pruebas deberían cubrir:

1. Afirmación y corrección confirmadas; mensaje y registro coherentes; historial
   anterior intacto; ninguna promoción automática de mensajes o texto del LLM.
2. Actor no autorizado, identidad suministrada por el cliente, clave no
   permitida y valor inválido: ningún cambio y ningún contenido en errores.
3. Predecesor de otra clave o generación, conflicto de versión y dos
   correcciones concurrentes: solo una queda vigente. En el experimento en
   memoria no se debe extrapolar su resultado a transacciones PostgreSQL.
4. Repetición exacta y repetición con contenido distinto; fallo simulado tras
   commit; `clear`, `delete` y recreación de una sesión con el mismo UUID.
5. Emisor autorizado que aporta un valor falso, interfaz manipulada o política
   simulada incorrecta: documentar que la atribución no valida la verdad ni
   demuestra seguridad de extremo a extremo.

Si después se aprobara persistencia real, serían necesarias pruebas separadas
contra PostgreSQL temporal para atomicidad, bloqueo y reintentos; migración y
permisos deberían revisarse antes de tocar una base existente. Los casos ya
utilizados para desarrollar el prototipo de recuperación no servirían como una
evaluación independiente de esta nueva operación.

## Siguiente fase propuesta

La demostración en proceso construye el mensaje canónico y modela correcciones,
reintentos y generaciones con política simulada. Para decidir si merece
persistencia real, primero se propone revisar sus límites frente a un consumidor
concreto y diseñar la migración, autorización y pruebas transaccionales. Esa
decisión requiere una fase posterior; la demostración no es autenticación de
producción ni prueba de comportamiento de PostgreSQL.
