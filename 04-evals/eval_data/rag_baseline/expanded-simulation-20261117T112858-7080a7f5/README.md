# Demostración de 24 intentos simulados

El 17 de noviembre de 2026 a las 11:28 (America/La_Paz) se realizó una
demostración con los 12 escenarios sintéticos y dos recorridos secuenciales.
Los 24 intentos terminaron: 16 corresponden a ataques y ocho a controles.

Cada intento usó una API HTTP simulada en loopback y una respuesta
predeterminada para su escenario. No se consultaron PostgreSQL, Ollama ni otros
modelos. Las dos repeticiones usan el mismo fixture: su coincidencia es parte
de la simulación, no una observación sobre la estabilidad de un modelo.

No se generaron revisiones. Al procesar los archivos con los evaluadores
existentes, los **16 ataques y ocho controles quedan pendientes de revisión**;
no se clasifican como ataques resistidos ni como controles aceptados.

- [Plan de intentos](plan.jsonl) y [casos materializados](cases.jsonl).
- [Expectativas de controles](expectations.jsonl) y [respuestas ficticias](outputs.jsonl).
- [Eventos](events.jsonl) e [informe con estados y huellas](report.json).
- [Revisiones de ataques vacías](attack_reviews.jsonl) y [revisiones de controles vacías](control_reviews.jsonl).
- [Diseño y procedimiento](../../../docs/security-expanded-evaluation-design.md).

El informe describe los bytes de esta instantánea. Las revisiones futuras
deben guardarse en archivos nuevos para conservar los hashes registrados.
