# Revisión de 03-Memory

**Revisión del 16 de noviembre de 2026.** El proyecto puede considerarse una
etapa didáctica utilizable para estudiar historial, recencia, persistencia y
fallos de recuperación. No está completo como sistema de memoria inteligente
para una aplicación real: las candidatas por relevancia no se han adoptado y
no existen identidad, autorización ni operación de producción.

## Qué está disponible

| Componente | Estado y alcance |
| --- | --- |
| Memoria en proceso | `ConversationMemory` conserva hasta 1.000 mensajes y selecciona un sufijo reciente contiguo, con límites de mensajes y caracteres. Es síncrona, supone un solo propietario y se pierde al terminar el proceso. |
| Memoria PostgreSQL | `PostgresConversationMemory` conserva sesiones explícitas, bloquea escrituras por sesión y lee cada contexto desde una instantánea. Abre una conexión por operación. |
| Recuperación léxica | Existe solo en un experimento con datos sintéticos. Recuperó información desactualizada y produjo una salida en un caso sin respuesta; no cumple los criterios propuestos para incorporarla. |
| Correcciones y procedencia | Hay prototipos locales y un esquema PostgreSQL experimental con identidad simulada. No son parte de la API de memoria persistente existente ni verifican la verdad de los datos. |
| Copia de historiales | Existe solo en el fixture temporal, para sesiones sintéticas expresamente registradas. No es una herramienta de migración real ni mantiene las copias sincronizadas. |
| Docker y CI | Compose permite una base local de desarrollo; el fixture usa PostgreSQL temporal aislado. GitHub Actions está configurado para pruebas, sin flujo de despliegue de Memory. |

La API de memoria consiste en clases Python; no hay API HTTP, consumidor de
modelos, integración con RAG o MCP ni servicio autónomo de Memory. El esquema
experimental es distinto de `memory_lab` y no asigna procedencia a mensajes
antiguos. La imagen de PostgreSQL usada incluye pgvector, pero Memory no lo
habilita ni lo usa.

## Evidencia revisada

En esta revisión se ejecutaron las pruebas en Windows con Python 3.10.11:

- **57 pruebas sin PostgreSQL**, con `python -S`; se descubrieron 41 pruebas de
  integración y se omitieron en esa ejecución.
- **41 pruebas de integración** con PostgreSQL 16 en un contenedor temporal:
  16 del almacenamiento existente, 15 de procedencia experimental y 10 de
  copia sintética. Se cubrieron concurrencia, límites, rollback, lecturas
  consistentes y respuestas perdidas simuladas después de un commit confirmado.
- Se verificaron la instantánea congelada de correcciones y la configuración de
  Compose. La matriz de CI configura Python 3.10 y 3.12; esta revisión local no
  ejecutó Python 3.12 ni observó una ejecución remota de los cambios actuales.

Los comandos de ejecución están en el [README de Memory](../README.md) y en
la [guía de la copia sintética](../tests/postgres_fixture/COPY-LAB.md). El
fixture usa datos y credenciales sintéticos, no los volúmenes persistentes del
usuario. Las pruebas no cubren todos los fallos posibles de red o almacenamiento.

La evaluación léxica inicial y la evaluación de correcciones usan muestras
pequeñas generadas y etiquetadas por el mismo asistente que conocía los métodos.
No son evaluaciones ciegas ni representativas. La candidata estructurada recibió
metadatos adicionales y falló cuando esos metadatos eran falsos; una puntuación
o un vínculo de procedencia no certifican la verdad. Los detalles están en los
[resultados léxicos](../experiments/retrieval/RESULTS.md) y la
[evaluación de correcciones](../experiments/corrections/HELDOUT.md).

## Límites que condicionan una integración

1. **Identidad y datos.** No hay autenticación, autorización por sesión,
   aislamiento entre usuarios, cifrado de contenido, política de retención ni
   borrado de copias de seguridad. Un UUID de sesión identifica datos; no
   autoriza acceso. Las identidades de procedencia son etiquetas simuladas.
2. **Identificadores y borrado.** En el almacenamiento PostgreSQL actual, una
   instancia antigua puede acceder a una sesión nueva si se reutiliza su UUID.
   Una lectura iniciada antes de `clear` o `delete` puede devolver una
   instantánea anterior. El esquema experimental usa generaciones, pero no
   cambia el contrato de la API existente.
3. **Reintentos y tiempo.** `append` del almacenamiento existente no es
   idempotente: tras una pérdida alrededor del commit, repetir puede duplicar
   mensajes. La procedencia experimental deduplica solo dentro de una
   generación activa. La copia sintética no es idempotente. Los límites de
   conexión y sentencia no garantizan un plazo total ante todos los fallos de red.
4. **Contexto y recuperación.** El presupuesto es en puntos de código, no en
   tokens, y puede comenzar con un mensaje del asistente sin su pregunta. La
   selección por recencia no resuelve contradicciones ni inyección de
   instrucciones. No hay una búsqueda semántica o una evaluación independiente
   que permita afirmar que la recuperación inteligente está resuelta.
5. **Operación y distribución.** No hay telemetría operativa ni una API de
   salud de Memory, política de respaldo/restauración o migración real. Los
   roles de prueba no establecen permisos seguros para otra base; `bytea` no
   cifra. El requisito directo de Psycopg está fijado por versión, pero no hay un
   bloqueo completo con hashes; la imagen PostgreSQL se referencia por etiqueta,
   no por digest.

Los cambios de procedencia, copia y CI de esta revisión están en el espacio de
trabajo; el estado local de Git muestra archivos modificados y archivos nuevos
sin seguimiento. No se ha verificado una ejecución de esos cambios en GitHub.

## Decisión propuesta

Cerrar **la etapa didáctica actual** de 03-Memory con estos límites explícitos,
sin presentar la recuperación inteligente, la migración o la seguridad como
terminadas. Un consumidor real requeriría definir su contrato e identidad,
revisar permisos y retención, y evaluar la recuperación sobre datos apropiados
antes de integrar los prototipos.

La siguiente fase propuesta es analizar **04-Evals** de forma independiente:
definir fundamentos, casos y una arquitectura mínima para evaluar sistemas LLM.
Los resultados de Memory pueden servir de ejemplos, pero no constituyen por sí
solos un framework de evaluación ni sustituyen una evaluación independiente.
