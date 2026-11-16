# AI Engineering Lab

Monorepositorio de aprendizaje práctico de ingeniería de IA. Cada proyecto será independiente y tendrá sus propias dependencias, pruebas y documentación.

## Estructura prevista

- `01-rag/`: ingesta, recuperación y generación aumentada.
- `02-mcp/`: servidores y herramientas MCP.
- `03-memory/`: memoria conversacional y persistente.
- `04-evals/`: evaluación de sistemas LLM.
- `05-agent-harness/`: ejecución y observabilidad de agentes.

Los proyectos posteriores se crearán cuando comience su desarrollo. La integración futura se realizará mediante contratos y APIs explícitos, evitando dependencias directas entre proyectos.

## Orden de trabajo

Se desarrolló primero [RAG](01-rag/README.md), cuyos límites y pendientes están documentados. [MCP](02-mcp/README.md) cuenta con un servidor stdio didáctico y pruebas limitadas con el SDK oficial de Python. [Memory](03-memory/README.md) ofrece historial y selección por recencia, con persistencia PostgreSQL opcional. Cada etapa se revisará antes de avanzar a la siguiente.
