# Copia de historiales sintéticos en el laboratorio

La utilidad [copy_synthetic.py](copy_synthetic.py) copia una sesión sintética
del esquema `memory_lab` a una sesión nueva de `memory_provenance_lab`, dentro
de la **misma base temporal** creada por el fixture. Conserva posición, rol y
bytes UTF-8; genera nuevos identificadores de mensaje y no crea afirmaciones,
actores ni registros de procedencia para los mensajes copiados.

Esta utilidad es parte de las pruebas. **No es una herramienta de migración de
datos reales**, no cambia el tráfico de la aplicación y no sincroniza las dos
sesiones después de copiar. Se debe seguir usando únicamente con datos
sintéticos en el fixture efímero.

## Ejecutar

Desde `03-memory`, con Docker Desktop activo y las dependencias opcionales
instaladas:

```powershell
.\.venv\Scripts\python.exe tests/postgres_fixture/run.py
```

El fixture crea PostgreSQL en un contenedor exclusivo con `tmpfs`, credenciales
y una marca aleatorias. Inicializa ambos esquemas, crea roles de prueba y
ejecuta las suites existentes y la de copia. La prueba marca explícitamente
como sintéticas las sesiones de origen que ella misma creó. Al terminar
normalmente detiene y elimina el contenedor; una terminación forzada puede
impedir la limpieza. No usa los volúmenes persistentes de Memory ni de RAG.

Antes de conectarse, la utilidad exige la marca de entorno del fixture y
argumentos válidos. Después comprueba el nombre de la base, el rol dedicado,
una marca aleatoria almacenada por el fixture y que la sesión esté registrada
como sintética. Estas comprobaciones reducen usos accidentales: no prueban que
un contenedor sea confiable y no resisten a un administrador que pueda
reproducir o alterar esas condiciones. La marca no es un mecanismo de
autenticación ni debe registrarse en logs.

## Qué hace la transacción

1. Comprueba el fixture y el registro sintético; valida la versión del esquema
   de destino. Después adquiere `SELECT ... FOR UPDATE` sobre la sesión de
   origen y lee los mensajes ordenados.
2. Comprueba posiciones consecutivas, roles, UTF-8 estricto y límites actuales.
   Rechaza una sesión corrupta o que exceda el límite sin crear un destino.
   Los datos se leen en memoria y están limitados a 1.000 mensajes.
3. Crea la sesión de destino con UUID y generación elegidos antes de iniciar la
   operación. Inserta los mensajes con IDs nuevos en la misma transacción y
   devuelve sus posiciones e identificadores después del commit.
4. Si el destino ya existe o falla cualquier escritura, la transacción revierte
   el destino y no modifica el origen. No sobrescribe ni hace reintentos
   automáticos.

El bloqueo protege la copia frente a escritores de origen que sigan el mismo
protocolo. La prueba coordina dos conexiones y observa que una escritura espera
el bloqueo; una vez termina la copia, la escritura continúa y las sesiones
pueden divergir. Un cliente con permisos SQL suficientes puede saltarse el
protocolo; el bloqueo no es un mecanismo de autorización.

Si la respuesta se pierde después del commit, el resultado puede ser incierto.
El llamador conserva los IDs de sesión y generación que eligió y debe verificar
el destino en la base temporal. Repetir la copia con un destino ya existente
produce conflicto: **la copia no es idempotente**. La prueba de respuesta
perdida introduce una excepción después de un commit confirmado; no reproduce
todas las fallas de red o almacenamiento.

## Permisos y pruebas

El rol sintético de copia puede leer los mensajes de origen y crear sesiones y
mensajes de destino. PostgreSQL exige privilegio `UPDATE` en alguna columna de
la sesión para `SELECT ... FOR UPDATE`; por eso este rol tiene `UPDATE` sobre
`session_id` y **no es de solo lectura** respecto al origen. La utilidad no
emite actualizaciones al origen, pero las concesiones no impiden que un cliente
SQL con esas credenciales intente saltarse el protocolo. Los permisos del
fixture no constituyen una política adecuada para una base de producción.

Las pruebas comparan origen y destino vacíos y con mensajes duplicados, Unicode,
NUL y un mensaje de tamaño máximo; comprueban que no se inventa procedencia.
También cubren errores de UTF-8 y posiciones, fallo después de crear destino o
insertar un mensaje, destino preexistente, marcas y sesiones no registradas,
respuesta perdida y escritura concurrente. Los errores se comprueban sin
exponer el contenido sintético ni las credenciales.

Una migración real requeriría otra decisión y otra implementación: validación
de esquemas y versiones existentes, autorización, respaldo y restauración,
control de escritores, reconciliación de resultados inciertos y plan de
retorno. Ninguna de esas garantías se deriva automáticamente de esta copia.
