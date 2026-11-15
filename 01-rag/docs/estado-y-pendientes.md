# 01-RAG: estado y pendientes

Esta revisión resume el proyecto desarrollado hasta ahora. Es un laboratorio funcional de un solo corpus; no es una certificación de preparación para producción.

## Qué está implementado

- Lectura de texto UTF-8, fragmentación por caracteres con solapamiento y límites de entrada.
- Embeddings y generación configurables con Ollama o OpenAI, sin mezclar los espacios vectoriales de ambos proveedores; comparación coseno y persistencia en PostgreSQL/pgvector.
- CLI de indexación, búsqueda y generación, y API local con credenciales separadas de lectura y escritura, fuentes recuperadas y eventos por etapa.
- Adaptador ASGI y un proxy TLS de laboratorio con proveedor simulado; ambos se prueban en un entorno aislado, no en un despliegue compartido.
- Conjuntos didácticos de evaluación, comparación exploratoria de recuperación y guía de revisión humana de respuestas.
- Política de permisos para un rol de base de datos restringido, carga de secretos de API mediante archivos y verificación de copia/restauración en bases temporales.

## Evidencia y límites

La suite automatizada cubre la lógica, errores, autorización, concurrencia y PostgreSQL cuando se proporciona la conexión de pruebas. CI está configurado para ejecutar la suite con Python 3.10 y 3.12 y, en 3.12, comprobar además permisos y restauración en un contenedor independiente. La ejecución local del verificador portable no demuestra que GitHub Actions ya haya ejecutado el flujo: eso depende de subir los cambios al repositorio y revisar el resultado allí.

Se probó una consulta completa con Ollama y una base real; las pruebas habituales simulan los proveedores. El cliente OpenAI se probó con respuestas simuladas y **no se ha verificado con credenciales ni una cuenta real**. Las pruebas TLS, carga y modelos reales son optativas, no se ejecutan en CI. La evaluación usa muy pocos documentos y preguntas; las citas y la fidelidad no se verifican automáticamente. Una respuesta `answered` puede carecer de sustento y requiere revisión de sus fuentes.

## Antes de un entorno compartido

- Seleccionar el entorno, las identidades, el procedimiento de certificados y secretos y los permisos efectivos de base; el prefijo interno de documentos no proporciona aislamiento por usuario o inquilino.
- Sustituir la configuración de proxy de prueba por una configuración propia del entorno, con TLS confiable, restricciones de red y límites validados. La implementación ASGI actual adapta el controlador HTTP previo y mantiene límites por proceso; no hay control global entre réplicas.
- Definir presupuestos totales de operación y comportamiento ante cancelaciones, saturación y escrituras de resultado incierto; los tiempos de lectura de red no garantizan que se cancele el trabajo de modelo.
- Definir y ensayar copias programadas, retención, cifrado y restauración, junto con objetivos RPO/RTO; la prueba actual solo restaura una copia sintética en un contenedor temporal.
- Ampliar la evaluación con documentos y preguntas representativos, un conjunto reservado nuevo y revisión de calidad. Repetir las mediciones de recursos y carga en el hardware y entorno objetivo; las pruebas sintéticas breves no establecen capacidad ni SLO.

La API y el proxy de laboratorio no deben exponerse a internet como configuración de producción. El resto de componentes del monorepositorio siguen pendientes y son independientes de esta revisión.
