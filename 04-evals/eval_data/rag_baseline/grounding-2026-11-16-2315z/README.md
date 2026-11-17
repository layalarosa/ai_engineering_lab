# Revisión didáctica de sustento de la línea base RAG

Esta revisión usa las respuestas y fragmentos del [informe RAG registrado](../run-2026-11-16-2315z/rag-report.json).
Las anotaciones fueron elaboradas por el **asistente**, sin revisión humana
independiente. No se hicieron llamadas a Ollama ni a OpenAI y no se reejecutó
RAG durante esta revisión. Los juicios ilustran el procedimiento y pueden ser discutidos; no
constituyen una referencia independiente para evaluar un juez automático.

## Lectura de los casos

| Caso | Revisión | Juicio y límite |
| --- | --- | --- |
| `chunking` | Completa, dos afirmaciones. | El fragmento `s1` apoya tanto conservar contexto como la posibilidad de recuperar información en los límites. |
| `embedding` | Completa, una afirmación. | El fragmento `s1` define el embedding como representación numérica generada por un modelo. |
| `pgvector` | **Incompleta**, dos afirmaciones provisionales. | `s1` apoya la búsqueda por distancia. La respuesta atribuye a pgvector la reutilización de vectores, mientras que el texto se refiere a la persistencia; anoté ese intervalo como `context` y dejé el caso pendiente para resolver la atribución. |
| `sin_respuesta` | Completa, rechazo explícito y ninguna afirmación factual puntuada. | La respuesta indica que los fragmentos no bastan para responder. Esta clasificación trata esa frase como una abstención sobre el contexto; requiere interpretación y no prueba una abstención correcta en general. |

En `pgvector`, el agregador muestra provisionalmente la primera afirmación como
`supported` y la segunda como `insufficient`, según las relaciones anotadas.
**Ambas quedan fuera de las métricas** porque la revisión está incompleta. Otra
persona podría considerar suficiente la evidencia de la segunda afirmación;
se necesita una decisión revisada antes de completar el caso. La respuesta
también omitió que pgvector permite almacenar embeddings. Esa omisión es una
cuestión de completitud y no se transforma aquí en una afirmación falsa o sin
sustento.

## Resultado del agregador

- Hay cuatro respuestas presentes y tres revisiones completas: la cobertura de
  revisión es `3/4`. La revisión pendiente corresponde a `pgvector`.
- Las tres afirmaciones puntuadas, dos de `chunking` y una de `embedding`,
  están anotadas como sustentadas: `supported_fraction = 3/3 = 1,0`. Las dos
  afirmaciones provisionales de `pgvector` se excluyen del denominador.
- Se registra una abstención explícita y un caso completo sin afirmaciones
  factuales puntuadas. El agregador no decide si la abstención es apropiada.

El valor `1,0` refleja **solo tres juicios de este asistente**. La división en
afirmaciones y el criterio de sustento influyen en el resultado; no es una
estimación de precisión general ni un aval de las fuentes. El código de salida
del agregador es `1` porque existe una revisión incompleta, no porque esa
revisión haya sido clasificada como falsa.

## Archivos y reproducción

- [Casos](cases.jsonl) y [respuestas con fuentes](outputs.jsonl): instantáneas
  que se usaron para los juicios. Las respuestas se obtuvieron mediante el
  adaptador offline a partir del informe registrado.
- [Revisiones](reviews.jsonl): proposiciones, intervalos, relaciones de
  evidencia y hashes de línea versión 2. Los índices cuentan puntos de código
  Unicode de Python y usan el extremo final exclusivo.
- [Informe de sustento](grounding-report.json) y [metadatos](metadata.json):
  resultados, hashes de entradas y código, procedencia y estado del árbol Git.

Desde `04-evals`, con `PYTHONPATH` apuntando a `src`:

```powershell
python -S -m evals_lab.grounding --cases eval_data/rag_baseline/grounding-2026-11-16-2315z/cases.jsonl --outputs eval_data/rag_baseline/grounding-2026-11-16-2315z/outputs.jsonl --reviews eval_data/rag_baseline/grounding-2026-11-16-2315z/reviews.jsonl
```

El evaluador valida formato, hashes e intervalos, no la relación semántica.
La pregunta o respuesta modificada invalida la revisión asociada. Los hashes
identifican los bytes revisados; no autentican al revisor ni prueban que su
criterio sea correcto. Los textos del informe son sintéticos, pero este formato
podría contener datos sensibles con otro corpus.
