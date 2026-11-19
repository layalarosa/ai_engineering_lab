# AI Engineering Lab

Laboratorio público de investigación práctica en ingeniería de IA. Explora cómo diseñar, probar y documentar componentes para aplicaciones con modelos de lenguaje: recuperación de información, herramientas, memoria conversacional y evaluación.

El repositorio prioriza implementaciones pequeñas que se puedan inspeccionar y medir. Cada módulo documenta su alcance, decisiones técnicas, evidencia disponible y limitaciones. Es un proyecto de aprendizaje e investigación en evolución; sus componentes no se presentan como sistemas listos para producción.

## Áreas de trabajo

| Módulo | Problema que explora | Implementación actual | Estado y límites |
| --- | --- | --- | --- |
| [01 — RAG](01-rag/README.md) | Recuperar contexto documental pertinente y usarlo al responder. | Ingesta y fragmentación, embeddings, búsqueda con pgvector, CLI y API, proveedores Ollama/OpenAI y evaluación de recuperación. | Laboratorio funcional de un corpus. La calidad, la fidelidad de las respuestas y la preparación para producción requieren validación adicional. [Estado y pendientes](01-rag/docs/estado-y-pendientes.md). |
| [02 — MCP](02-mcp/README.md) | Conectar aplicaciones con herramientas mediante Model Context Protocol. | Servidor local Python por `stdio`, herramientas didácticas, integración opcional con RAG y pruebas de interoperabilidad acotadas con el SDK oficial. | Implementa un subconjunto documentado; las pruebas con un cliente y transporte concretos no certifican conformidad completa. |
| [03 — Memory](03-memory/README.md) | Conservar historial y seleccionar contexto conversacional útil. | Memoria de proceso, selección por recencia y persistencia PostgreSQL opcional. | La recuperación por relevancia se ha explorado con datos sintéticos, pero no forma parte de la API actual. |
| [04 — Evals](04-evals/README.md) | Medir comportamientos de sistemas de IA con casos y criterios explícitos. | Evaluadores locales de coincidencia exacta, recuperación, sustento y cobertura; utilidades de seguridad y latencia, ejemplos offline y CI. | Los datos son principalmente sintéticos o registrados. Las métricas y revisiones parciales no demuestran calidad o seguridad general. |

## Enfoque de ingeniería

- **Alcance explícito:** cada módulo explica qué implementa y qué queda fuera.
- **Componentes aislados:** los proyectos mantienen sus propias dependencias y pruebas; las integraciones entre ellos usan contratos claros cuando aportan valor.
- **Evidencia antes que afirmaciones:** se documenta si una comprobación usó modelos reales, servicios simulados o datos sintéticos.
- **Límites visibles:** se describen riesgos, supuestos y pendientes para evitar confundir un prototipo de investigación con una solución validada para producción.
- **Costos controlables:** el trabajo local usa herramientas como Python, Docker, PostgreSQL/pgvector y Ollama; los proveedores externos son opcionales y pueden generar cargos según su uso.

## Estructura del repositorio

```text
01-rag/       Recuperación aumentada por generación
02-mcp/       Servidor y herramientas MCP
03-memory/    Historial y selección de memoria
04-evals/     Evaluación local de sistemas de IA
.github/      Flujos de integración continua por módulo
```

Cada carpeta de proyecto contiene instrucciones propias. Revisa su README antes de instalar dependencias o ejecutar pruebas: algunos flujos son completamente locales y otros requieren Docker, PostgreSQL/pgvector, Ollama o variables de entorno opcionales.

## Cómo empezar

1. Elige el área que quieres estudiar en la tabla anterior.
2. Lee el README y, cuando exista, el documento de estado o diseño vinculado desde ese módulo.
3. Sigue sus pasos locales y distingue las pruebas automatizadas de las evaluaciones manuales o experimentales.
4. Registra cambios de configuración, datos y modelo al comparar resultados; una métrica solo es interpretable dentro de las condiciones en que se obtuvo.

Los flujos de GitHub Actions están definidos por separado para [RAG](.github/workflows/rag-ci.yml), [MCP](.github/workflows/mcp-ci.yml), [Memory](.github/workflows/memory-ci.yml) y [Evals](.github/workflows/evals-ci.yml). La configuración en el repositorio no implica que una ejecución remota haya pasado: el estado vigente se consulta en GitHub Actions.

## Alcance de investigación

Este laboratorio sirve para estudiar decisiones de diseño y construir prototipos reproducibles. Sus conjuntos de evaluación no representan todos los usuarios, documentos, modelos o ataques posibles. Una ejecución local, un resultado simulado o una prueba sintética no deben interpretarse como una garantía de exactitud, seguridad, rendimiento ni disponibilidad en producción.

## Licencia

El código original de este repositorio se distribuye bajo la [licencia MIT](LICENSE). Las dependencias, modelos y datos de terceros pueden tener términos propios; consulta sus licencias y condiciones por separado.
