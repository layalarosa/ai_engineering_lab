# Permisos, secretos y restauración de PostgreSQL

Este documento describe la preparación y una prueba **de laboratorio**. No se ha modificado ninguna base existente ni se ha configurado un sistema de copias para producción.

## Separación de responsabilidades

La extensión `vector` y las tablas se inicializan mediante una identidad administrativa. La API utiliza después un rol `rag_runtime` independiente, sin privilegios administrativos, que no es propietario de la base ni de las tablas. La política de ejemplo en [`ops/grant_runtime.sql`](../ops/grant_runtime.sql) concede:

- `USAGE` del esquema `public`.
- `SELECT` e `INSERT` en `rag_models`.
- `SELECT`, `INSERT` y `DELETE` en `rag_chunks`, necesarios para sustituir fragmentos en una transacción.

El SQL requiere que el rol exista previamente con `LOGIN`, sin membresías y sin capacidades administrativas. Rechaza ejecutarse si el rol puede crear objetos en `public` o en la base, es propietario de la base o tablas, o conserva permisos efectivos adicionales en las tablas, incluidos los heredados de `PUBLIC`. El script revoca las concesiones directas del rol sobre **esas dos tablas** y aplica las concesiones indicadas; úsalo solo tras revisar sus efectos en una base dedicada. No audita todos los objetos de la base ni revoca permisos globales de `PUBLIC` o concesiones sobre otras tablas. PostgreSQL distingue al propietario de un objeto de los roles a los que se conceden permisos; la propiedad permite modificar o eliminar el objeto. [Documentación de privilegios](https://www.postgresql.org/docs/16/ddl-priv.html)

Antes de un despliegue real, el administrador debe aprovisionar el rol y su autenticación fuera del repositorio, restringir la conexión de red y revisar los permisos efectivos, incluidos `PUBLIC`, objetos adicionales y los permisos de conexión. La cuenta de ejecución no debe utilizarse para inicializar ni restaurar el esquema. El ejemplo supone el esquema `public` y tablas existentes; no configura migraciones ni permisos futuros automáticamente.

## Secretos montados en archivos

La API local y ASGI aceptan `DATABASE_URL_FILE`, `RAG_WRITE_TOKEN_FILE`, `RAG_READ_TOKEN_FILE` y `OPENAI_API_KEY_FILE` como alternativas a las variables sin sufijo. Cada archivo contiene solo el valor en UTF-8; se eliminan saltos de línea finales. El archivo se limita a 16.384 bytes, debe poder leerse y no puede estar vacío. Si se configuran simultáneamente una variable y su variante `_FILE`, el inicio o la operación falla sin mostrar el valor ni la ruta del archivo.

Por ejemplo, un entorno de ejecución podría montar archivos solo de lectura con permisos adecuados y definir las rutas en las variables `_FILE`. No incluyas los secretos en la imagen, repositorio, argumentos de procesos ni registros. Montar archivos no sustituye la protección del host ni un gestor de secretos; administradores y procesos con acceso suficiente todavía pueden leerlos.

La configuración de base de datos y los dos tokens se cargan al iniciar la API: para rotarlos, actualiza la provisión y reinicia el proceso de forma controlada. La clave OpenAI se vuelve a leer en cada llamada, además de comprobarse al iniciar si se usa ese proveedor. No hay una función de rotación sin interrupciones ni un periodo de doble credencial. El CLI administrativo sigue usando `DATABASE_URL`; no interpreta `DATABASE_URL_FILE`.

## Copia y restauración

La prueba aislada crea dos bases nuevas en un contenedor temporal. Inicializa la primera, concede los permisos al rol de prueba, indexa un documento sintético, genera una copia en formato `custom` con `pg_dump` y la restaura en la segunda con `pg_restore --exit-on-error --no-owner --no-acl`. Después aplica de nuevo las concesiones y comprueba que se conservan el texto y el vector, que la búsqueda funciona y que el rol restringido puede volver a indexar. También prueba la consulta HTTP de la API con ese rol y modelos simulados en ambas bases.

`pg_dump` crea una copia coherente de una sola base, pero no incluye los roles globales del clúster. La restauración necesita la extensión pgvector compatible disponible en el destino, una identidad administrativa y el aprovisionamiento separado de roles y permisos. [Documentación de pg_dump](https://www.postgresql.org/docs/16/app-pgdump.html), [documentación de pg_restore](https://www.postgresql.org/docs/16/app-pgrestore.html). Restaurar un archivo ejecuta instrucciones procedentes de la fuente: acepta solo copias de confianza y restaura primero en una base aislada.

La prueba no configura copias periódicas, retención, cifrado, recuperación a un instante concreto, copias fuera del host ni objetivos RPO/RTO. Para datos reales deben definirse y comprobarse esos controles, junto con protección de la copia, versiones compatibles, capacidad de almacenamiento y ensayos de restauración. No restaures sobre una base de usuario sin un procedimiento y una decisión explícitos.

## Repetir la verificación

Con Docker activo y las dependencias instaladas, desde `01-rag`:

```powershell
python tests/postgres_fixture/run.py
```

El mismo verificador se ejecuta en CI para Python 3.12. Crea un contenedor con nombre único y dos bases nuevas dentro de él, con contraseñas temporales generadas para la prueba. Valida también que una concesión no deseada a `PUBLIC` haga fallar la política. El archivo de copia permanece dentro del contenedor temporal y se elimina con este. El guion no usa `DATABASE_URL` existente como destino ni modifica variables de entorno del proceso llamador. No es una herramienta de respaldo para bases reales.
