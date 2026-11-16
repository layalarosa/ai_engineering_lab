# Diseño: memoria conversacional persistente con PostgreSQL

**Estado: primera implementación probada con PostgreSQL temporal.** Este
documento conserva el diseño de la persistencia; `ConversationMemory` sigue
disponible en proceso. La implementación no conecta un modelo, no realiza
búsqueda semántica ni modifica las tablas de `01-rag`.

## Objetivo y límites

Una aplicación local de confianza necesita guardar mensajes, cerrar el proceso
y recuperar después la misma sesión. Se mantiene la selección por recencia y
el límite de 1.000 mensajes por sesión. PostgreSQL será la fuente de verdad:
cada operación consulta o modifica la base, sin conservar una copia en memoria
que pueda quedar desactualizada.

El alcance inicial es una biblioteca Python y una base de laboratorio
dedicada. No se expone una API web ni se implementan autenticación de usuarios,
aislamiento entre organizaciones, cifrado de la base o autorización por
sesión. Un UUID identifica una sesión; **no demuestra quién puede acceder a
ella**. La prueba usará datos sintéticos y una base temporal exclusiva.

## Interfaz implementada

Se conserva `ConversationMemory` para uso exclusivamente en proceso. Una
clase concreta adicional, `PostgresConversationMemory`, maneja una sesión
persistente. No se introduce una interfaz abstracta ni un ORM en esta etapa.
La selección de contexto y la validación usan funciones pequeñas compartidas
por ambas clases, conservando las pruebas y el comportamiento existentes.

| Operación | Resultado |
| --- | --- |
| `create(dsn, session_id)` | Crea una sesión vacía con un `uuid.UUID` elegido por el llamador y devuelve una instancia asociada. Si ya existe, falla sin sobrescribirla. |
| `open(dsn, session_id)` | Comprueba que la sesión existe y devuelve una instancia asociada. No crea sesiones implícitamente. |
| `append(role, content)` | Guarda un mensaje nuevo y conserva el orden de la sesión. |
| `context(max_chars=4000, max_messages=20)` | Devuelve la misma estructura `messages` y `omitted_count` de la versión en memoria. |
| `clear()` | Elimina los mensajes de la sesión, pero conserva su identificador. |
| `delete()` | Elimina la sesión y sus mensajes. Las operaciones posteriores con ese identificador fallan si no existe. |

El llamador puede generar el identificador antes de crear la sesión con
`uuid.uuid4()` y conservarlo para reabrirla. No se usará ese valor como
credencial. Las instancias que apuntan a un mismo ID observan la misma sesión;
una sesión eliminada no se recrea automáticamente. Crear otra sesión requiere
una operación explícita; se recomienda generar un UUID nuevo. Como este diseño
no conserva una marca de los identificadores borrados, no impide reutilizar
un UUID eliminado: una instancia antigua con ese ID podría entonces acceder a
la nueva sesión. Impedir esa reutilización requeriría un mecanismo adicional y
es una decisión pendiente para un entorno compartido.

Los mismos argumentos válidos e inválidos de la memoria actual conservarán su
significado: roles `user` y `assistant`, mensajes duplicados permitidos, texto
sin alterar, límites de caracteres y mensajes, y error si el mensaje más
reciente no cabe en el presupuesto. `ValueError` indicará entrada inválida o
intento de crear un ID existente; `LookupError`, una sesión inexistente;
`RuntimeError`, una sesión llena. Se usa una excepción específica
`MemoryStorageError`, derivada de `RuntimeError`, para fallos de base de datos
o datos almacenados inválidos, con mensajes que no contengan contenido ni
credenciales. El resultado de `context` será una copia independiente.

## Esquema de datos

Para este laboratorio se usa un esquema propio y solo dos tablas:

```sql
CREATE SCHEMA memory_lab;

CREATE TABLE memory_lab.sessions (
    session_id uuid PRIMARY KEY
);

CREATE TABLE memory_lab.messages (
    session_id uuid NOT NULL
        REFERENCES memory_lab.sessions(session_id) ON DELETE CASCADE,
    position integer NOT NULL CHECK (position BETWEEN 1 AND 1000),
    role text NOT NULL CHECK (role IN ('user', 'assistant')),
    content_utf8 bytea NOT NULL
        CHECK (octet_length(content_utf8) BETWEEN 1 AND 16000),
    PRIMARY KEY (session_id, position)
);
```

El esquema se inicializa explícitamente en una base dedicada y vacía para
esta finalidad. La inicialización es una transacción: si `memory_lab` ya
existe, falla sin modificarlo; no usa `DROP` ni supone que una tabla existente
tiene la estructura esperada. No se ejecuta al importar el módulo. La gestión
de versiones y migraciones requiere un diseño posterior antes de cambiar este
esquema.

### Por qué `bytea` para el contenido

La memoria actual acepta texto Python UTF-8 que contiene U+0000. Una columna
`text` de PostgreSQL no puede almacenar NUL, por lo que rechazaría un mensaje
válido para la implementación actual. `bytea` permite conservar los bytes
UTF-8 exactamente; la aplicación codifica al escribir y decodifica en modo
estricto al leer. Psycopg adapta `bytes` a `bytea`. [Tipos de caracteres de PostgreSQL](https://www.postgresql.org/docs/16/datatype-character.html),
[adaptación de Psycopg](https://www.psycopg.org/psycopg3/docs/basic/adapt.html#binary-adaptation).

El límite de 16.000 bytes corresponde a un máximo de 4.000 puntos de código
UTF-8 de hasta cuatro bytes cada uno. El `CHECK` de bytes **no valida por sí
solo** UTF-8, 4.000 puntos de código ni la regla de espacios: las mismas
validaciones deben aplicarse en Python antes de escribir y al recuperar datos.
Una lectura corrupta falla explícitamente; no se reemplazan bytes ni se omite
el mensaje. `bytea` no cifra contenido y complica búsquedas SQL de texto: si
después se necesita búsqueda léxica, se deberá diseñar cómo indexar sin perder
el contenido original ni aceptar equivalencias incorrectas.

## Orden y escrituras concurrentes

Las operaciones `append`, `clear` y `delete` sobre una sesión usan una
transacción `READ COMMITTED` y adquieren primero un bloqueo de fila de la
sesión con `SELECT ... FOR UPDATE`. En `append` se consultan la cantidad y la
posición máxima **después** de adquirir el bloqueo; si ya hay 1.000 mensajes,
se rechaza la inserción. En caso contrario, se inserta con la siguiente
posición y se confirma la transacción. Un fallo anterior al commit revierte la
operación completa.

El bloqueo serializa estas operaciones solo si **todos los escritores** usan
el mismo protocolo. El orden de mensajes confirmados de una sesión sigue el
orden de adquisición del bloqueo, no necesariamente el orden de llegada de
solicitudes. PostgreSQL mantiene el bloqueo de fila hasta terminar la
transacción. [Bloqueos de fila](https://www.postgresql.org/docs/16/explicit-locking.html#LOCKING-ROWS).

`clear` adquiere el mismo bloqueo y elimina los mensajes, de manera que la
siguiente inserción en la sesión vacía empieza en la posición 1. `delete`
adquiere el bloqueo y elimina la fila de sesión; la clave foránea elimina sus
mensajes. Si una inserción confirma antes de un `delete`, este puede borrarla
después; si obtiene el bloqueo después de que la sesión se haya eliminado,
falla como sesión inexistente. No hay eliminación individual de mensajes en el
primer alcance.

## Lectura consistente del contexto

`context` valida primero sus límites. Después abre una transacción de
solo lectura `REPEATABLE READ` para comprobar la sesión, contar los mensajes
y obtener como máximo los `max_messages` más recientes mediante `ORDER BY
position DESC`. Los tres resultados corresponden a la misma instantánea; se
aplica en Python la selección contigua por caracteres y se invierte la lista
para devolverla en orden cronológico. `omitted_count` es el total de esa
instantánea menos los mensajes efectivamente devueltos.

Con `READ COMMITTED`, dos `SELECT` de una misma transacción pueden observar
commits distintos; `REPEATABLE READ` mantiene una instantánea coherente para
estas lecturas. [Aislamiento de transacciones](https://www.postgresql.org/docs/16/transaction-iso.html).
Una lectura que comenzó antes de un `clear` o `delete` puede terminar después
de él y todavía devolver su instantánea anterior. Una nueva transacción
iniciada después del commit observará el nuevo estado. La eliminación no
revoca información que un consumidor ya leyó.

## Configuración, fallos y seguridad

- Se usa `psycopg` 3 como dependencia directa para PostgreSQL, sin ORM ni
  extensión pgvector en esta etapa. Las pruebas en memoria seguirán siendo
  ejecutables por separado sin una base activa.
- La aplicación puede recibir el DSN por `MEMORY_DATABASE_URL` o, como alternativa,
  por el archivo indicado en `MEMORY_DATABASE_URL_FILE`; configurar ambos es
  un error. No se imprimirán el DSN, la ruta del secreto ni el contenido de
  los mensajes. Las variables de entorno y los archivos siguen necesitando
  permisos apropiados en el equipo.
- Se abre una conexión independiente por operación, sin pool para
  este alcance. Inicialización y ejecución usarían roles separados. El rol de
  ejecución necesitará permisos explícitos sobre el esquema y las tablas;
  `SELECT ... FOR UPDATE` también exige `UPDATE` en al menos una columna de la
  tabla bloqueada, algo que debe comprobarse al diseñar el rol mínimo.
  [Privilegios de `SELECT` con bloqueo](https://www.postgresql.org/docs/16/sql-select.html).
- Como límites iniciales de laboratorio se usan 10 segundos de conexión,
  `lock_timeout` de 5 segundos y `statement_timeout` de 10 segundos por
  sentencia. No son un plazo total de la operación ni garantizan terminar una
  espera de red en todos los fallos. Los errores se devuelven al llamador; no
  hay reintentos automáticos.
- Si se pierde la conexión durante o después de `COMMIT`, el resultado puede
  ser incierto. `append` no es idempotente: repetirlo puede duplicar mensajes,
  y comparar contenido no siempre resuelve la incertidumbre. No se promete
  *exactly once*. La creación usa un ID elegido de antemano para poder
  consultar si existe, sin tratarlo como autorización.
- `clear` y `delete` eliminan datos activos en la transacción; no garantizan
  borrado inmediato de archivos físicos, réplicas o copias de seguridad. No
  hay retención automática ni límite global de sesiones en esta implementación.

## Pruebas de aceptación

1. Mantener las pruebas actuales de memoria en proceso y ejecutar los mismos
   casos de selección sobre PostgreSQL, incluidas ventanas exactas, omisiones,
   texto con espacios, emojis y NUL.
2. Crear dos sesiones en una base de pruebas dedicada; cerrar y reabrir el
   proceso o conexión y verificar persistencia, separación, orden, `clear`,
   `delete`, sesión inexistente, intento de creación duplicada y el
   comportamiento explícito al reutilizar un UUID borrado.
3. Probar el límite de 1.000 mensajes sin pérdida y con dos conexiones que
   intenten escribir simultáneamente cerca del límite. Verificar secuencias
   únicas y ausencia de mensajes parciales tras un rollback.
4. Coordinar lecturas con `append`, `clear` y `delete` para comprobar que
   `context` devuelve una instantánea coherente y que una lectura nueva ve el
   cambio confirmado. Probar el caso de escritura en espera durante un delete.
5. Comprobar credenciales ausentes o ambiguas, conexión y bloqueos fallidos,
   datos `bytea` inválidos introducidos por un test controlado y errores sin
   contenido o secretos. Cubrir el resultado incierto sin suponer reintentos
   seguros ni `exactly once`.
6. Usar una instancia PostgreSQL temporal y aislada; no crear, modificar ni
   borrar esquemas o volúmenes existentes del usuario. Mantener las pruebas
   sin PostgreSQL separadas y repetirlas sin ese servicio.

La implementación dispone de un laboratorio Docker temporal y de un Compose
de desarrollo independiente. Las pruebas verificaron los casos descritos,
incluido un fallo inyectado después del commit; esto no reproduce todos los
fallos posibles de red o almacenamiento. Las instrucciones de ejecución y los
límites operativos están en el [README](../README.md#persistencia-con-postgresql).
