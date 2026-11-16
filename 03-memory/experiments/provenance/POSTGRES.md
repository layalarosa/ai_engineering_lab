# Procedencia con PostgreSQL temporal

Este experimento guarda afirmaciones y correcciones en el esquema separado
`memory_provenance_lab`. La política de actores continúa siendo **simulada**:
un nombre permitido no autentica a una persona ni protege contra un cliente
con acceso directo a las tablas. El experimento está pensado para datos
sintéticos y bases temporales, no para usuarios reales.

El esquema y la clase `PostgresConversationMemory` existentes no se modifican.
No hay migración de historiales reales. El fixture incluye una
[copia de historiales sintéticos](../../tests/postgres_fixture/COPY-LAB.md)
para estudiar la transición en la base temporal.

## Ejecutar las pruebas

Desde `03-memory`, con Docker Desktop activo y la dependencia opcional ya
instalada desde `requirements-postgres.txt`:

```powershell
.\.venv\Scripts\python.exe tests/postgres_fixture/run.py
```

El fixture crea un contenedor PostgreSQL exclusivo con almacenamiento temporal,
credenciales sintéticas y un puerto local aleatorio. Inicializa por separado
`memory_lab` y `memory_provenance_lab`, crea un rol de ejecución para cada
esquema, además de un rol específico de copia, y ejecuta las tres suites de
integración. Una de las pruebas crea y
elimina además una base de nombre aleatorio dentro de ese mismo contenedor,
para comprobar un fallo durante la inicialización. No usa los volúmenes de
desarrollo ni de RAG. Al terminar normalmente elimina el contenedor; una
terminación forzada puede impedir la limpieza.

La suite de procedencia solo se habilita cuando el fixture proporciona sus
dos conexiones y la marca `MEMORY_PROVENANCE_TEMPORARY=1`. Sin ese entorno se
omite. Las pruebas locales de configuración se ejecutan sin PostgreSQL ni
Psycopg:

```powershell
python -S -m unittest discover -s tests -p test_postgres_provenance_config.py -v
```

El flujo de GitHub Actions está configurado para ejecutar las tres suites mediante
el fixture en Python 3.12. Esto no implica que el flujo se haya ejecutado ya en
GitHub para los cambios locales.

## Contrato experimental

`postgres_lab.initialize(dsn)` crea el esquema y la versión 1 en una única
transacción y falla si el esquema ya existe. Debe usarse con el administrador
de una base temporal dedicada. La clase `PostgresProvenanceLab` recibe una
conexión y un conjunto de nombres simulados permitidos; el llamador controla
esos nombres, por lo que la comprobación no es seguridad real.

- `create_session` crea una sesión y devuelve una generación nueva. `append`
  añade un mensaje normal sin crear procedencia. `context` aplica los mismos
  límites y selección por recencia del módulo existente.
- `confirm` acepta las dos claves de la demostración en memoria, un valor,
  generación e ID de operación. Genera un mensaje canónico y guarda en una
  transacción el mensaje, la afirmación, el puntero vigente y la operación.
  Una corrección debe referenciar la afirmación vigente de la misma clave.
- Un reintento con la misma sesión, generación, actor simulado, ID y solicitud
  estable devuelve la afirmación anterior. Reutilizar el ID con contenido o
  predecesor distinto produce conflicto. La comparación ocurre antes de revisar
  la versión vigente o el límite de mensajes.
- `current` lee el puntero y el mensaje de una instantánea consistente. `clear`
  borra los registros de la generación y crea otra; `delete` elimina la sesión.
  Una solicitud de una generación anterior falla, incluso si se reutiliza el
  UUID de sesión. Después de borrar no se conserva el registro de deduplicación.

La lectura y las escrituras nuevas comprueban que el puntero corresponde a una
única cadena cronológica. Si faltara el puntero o aparecieran raíces no
relacionadas, el experimento informa inconsistencia en vez de elegir una
versión silenciosamente. Esta comprobación no detecta afirmaciones falsas ni
protege contra todas las modificaciones posibles mediante SQL directo.

Se serializan las escrituras que siguen el protocolo mediante un bloqueo de la
fila de sesión. Las restricciones de clave foránea y unicidad ayudan a evitar
referencias inconsistentes, pero un cliente con privilegios SQL de escritura
puede intentar operar al margen del protocolo. El rol sintético usado por el
fixture no puede crear ni truncar tablas y no tiene acceso al esquema del otro
experimento; esto no garantiza aislamiento entre usuarios ni privilegios
mínimos en una base distinta.

Cada operación abre una conexión; se usan los límites de conexión y sentencia
del módulo PostgreSQL existente. No hay plazo total garantizado para todos los
fallos de red ni reintentos automáticos. Una excepción durante o después del
commit puede dejar el resultado incierto. La prueba de respuesta perdida inyecta
un error **después de un commit confirmado** y verifica el reintento: no
reproduce todas las fallas posibles de red o almacenamiento.

El contenido y las identidades simuladas siguen siendo datos sensibles en una
base real; `bytea` no cifra. Los errores del controlador se devuelven saneados.
Una lectura iniciada antes de un borrado puede terminar después con una
instantánea previa, y borrar registros activos no garantiza borrar copias de
seguridad ni información ya entregada.

## Verificaciones cubiertas

Las pruebas usan conexiones reales para correcciones competidoras, reintentos
simultáneos y límite de mensajes; inyectan fallos después de cada escritura y
antes de confirmar; verifican el rollback y una respuesta perdida simulada.
También comprueban aislamiento entre esquemas, restricciones de claves,
lecturas durante correcciones, Unicode y NUL, borrado de una cadena y reuso de
UUID. Esas comprobaciones solo aplican al entorno y escenarios de prueba.

La copia de datos reales, autenticación real, revisión de permisos para un
entorno concreto y migración de datos quedan fuera de esta etapa.
