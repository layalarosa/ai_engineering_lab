# Preparación de una evaluación local con RAG

**Estado: ejecutada una vez con modelos locales.** Los resultados se registran
en [la observación de la ejecución](../eval_data/rag_baseline/run-2026-11-16-2315z/results.md).
Esta guía usa solo el corpus sintético existente y la configuración fijada abajo. Las
etiquetas y su justificación están en [labels.md](../eval_data/rag_baseline/labels.md);
fueron revisadas por el asistente, sin revisión humana independiente. El
resultado servirá para aprender el recorrido, no para afirmar calidad general.

## Experimento fijado

| Parámetro | Valor |
| --- | --- |
| Corpus | `fragmentacion`, `embeddings`, `persistencia`, exactamente los tres archivos `01-rag/eval_data/*.txt` nombrados. |
| Casos | `01-rag/eval_data/api_cases.json` y las etiquetas correspondientes en `04-evals/eval_data/rag_baseline/cases.jsonl`. |
| Embeddings | Ollama local, `nomic-embed-text:latest`. |
| Generación | Ollama local, `llama3.1:8b`. |
| Recuperación | `limit=2` fragmentos; sin umbral de similitud; `k=2` documentos en Evals. |
| Almacenamiento | Contenedor PostgreSQL/pgvector nuevo, dedicado y efímero; sin reutilizar volúmenes o bases anteriores. |

Cada documento actual genera un solo fragmento; por ello esta ejecución no
permitirá observar cambios de rango por deduplicación. Sin umbral, una pregunta
sin evidencia puede recibir fuentes irrelevantes: una respuesta `answered` no
demuestra que el modelo haya contestado ni que se haya abstenido.

## Preparación observada

Al preparar esta guía se comprobó Docker Desktop, la imagen local
`pgvector/pgvector:0.8.0-pg16`, los dos modelos en Ollama y la dependencia
`psycopg` dentro de `01-rag/.venv`. Son comprobaciones de disponibilidad de ese
momento; hay que repetirlas antes de ejecutar de nuevo. En la ejecución
posterior se usó una base nueva y se eliminó el contenedor al terminar.

## Procedimiento para repetir la ejecución

Ejecuta en una terminal PowerShell **nueva y dedicada** desde la raíz del
monorepo. El procedimiento crea recursos con identificadores nuevos, sin
reutilizar contenedores o volúmenes. Si un paso falla, detén la ejecución y
conserva el error; no uses un informe parcial. Las credenciales quedan en el
entorno de esta terminal y sus procesos hijos. Los archivos de resultados se
escriben fuera del repositorio, en un directorio temporal único.

### 1. Configurar la sesión y comprobar requisitos

```powershell
$ErrorActionPreference = 'Stop'
$labRoot = (Get-Location).Path
$labRag = Join-Path $labRoot '01-rag'
$labEvals = Join-Path $labRoot '04-evals'
$labPython = Join-Path $labRag '.venv/Scripts/python.exe'
if (-not (Test-Path $labPython)) { throw 'Falta el entorno de Python de RAG.' }
& $labPython -c "import psycopg"
if ($LASTEXITCODE -ne 0) { throw 'Falta psycopg en el entorno de RAG.' }
docker version --format '{{.Server.Version}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker no está disponible.' }
docker image inspect pgvector/pgvector:0.8.0-pg16 --format '{{.Id}}'
if ($LASTEXITCODE -ne 0) { throw 'Falta la imagen local de pgvector.' }
ollama list
if ($LASTEXITCODE -ne 0) { throw 'Ollama no está disponible.' }
```

Confirma en la lista que están ambos modelos indicados. Conserva sus IDs y la
salida de `git rev-parse HEAD` y `git status --short` para identificar la
versión y los cambios locales. Los IDs de modelo y una etiqueta de imagen no
garantizan reproducibilidad bit a bit. No se descargan modelos en estos pasos.

### 2. Crear una base y una API aisladas

```powershell
$labId = [guid]::NewGuid().ToString('N')
$labContainer = "ai-evals-$labId"
$labDir = Join-Path ([IO.Path]::GetTempPath()) "ai-evals-$labId"
New-Item -ItemType Directory -Path $labDir | Out-Null
$env:POSTGRES_PASSWORD = & $labPython -c "import secrets; print(secrets.token_hex(24))"
$env:RAG_WRITE_TOKEN = & $labPython -c "import secrets; print(secrets.token_urlsafe(32))"
$env:RAG_READ_TOKEN = & $labPython -c "import secrets; print(secrets.token_urlsafe(32))"
docker run -d --rm --name $labContainer -e POSTGRES_USER=rag -e POSTGRES_DB=rag -e POSTGRES_PASSWORD -p '127.0.0.1::5432' pgvector/pgvector:0.8.0-pg16
if ($LASTEXITCODE -ne 0) { throw 'No se pudo iniciar PostgreSQL.' }
$labReady = $false
for ($i = 0; $i -lt 30; $i++) {
  docker exec $labContainer pg_isready -U rag -d rag 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { $labReady = $true; break }
  Start-Sleep -Seconds 1
}
if (-not $labReady) { throw 'PostgreSQL no quedó disponible.' }
$labBinding = docker port $labContainer 5432/tcp
if ($LASTEXITCODE -ne 0 -or $labBinding -notmatch '^127\.0\.0\.1:(\d+)$') { throw 'Puerto de base inesperado.' }
$labDbPort = $Matches[1]
$env:DATABASE_URL = "postgresql://rag:$($env:POSTGRES_PASSWORD)@127.0.0.1:$labDbPort/rag"
$env:PYTHONPATH = Join-Path $labRag 'src'
$env:PYTHONUTF8 = '1'
$env:RAG_EMBED_PROVIDER = 'ollama'
$env:RAG_GENERATION_PROVIDER = 'ollama'
$env:RAG_EMBED_MODEL = 'nomic-embed-text:latest'
$env:RAG_GENERATION_MODEL = 'llama3.1:8b'
$env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434'
Remove-Item Env:RAG_MIN_SCORE -ErrorAction SilentlyContinue
Remove-Item Env:DATABASE_URL_FILE,Env:RAG_READ_TOKEN_FILE,Env:RAG_WRITE_TOKEN_FILE -ErrorAction SilentlyContinue
& $labPython -m rag.database_cli init
if ($LASTEXITCODE -ne 0) { throw 'No se pudo inicializar el esquema.' }
$env:RAG_PORT = & $labPython -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()"
$labServer = Start-Process -FilePath $labPython -ArgumentList @('-u','-m','rag.api') -WorkingDirectory $labRag -WindowStyle Hidden -RedirectStandardOutput (Join-Path $labDir 'api-out.log') -RedirectStandardError (Join-Path $labDir 'api-error.log') -PassThru
```

El puerto de la API se elige libre y se libera antes de iniciarla: existe una
pequeña posibilidad de que otro proceso lo ocupe entre ambos pasos. Antes de
enviar documentos, verifica que el proceso siga activo y que su archivo
`api-error.log` indique que escucha en el puerto elegido. Si no, detente. La
API está limitada a localhost; sus registros y el informe pueden contener
datos y deben tratarse de acuerdo con su contenido.

### 3. Indexar y obtener el informe

Solo después de comprobar la API, en la misma terminal:

```powershell
if ($labServer.HasExited) { throw 'La API terminó antes de indexar.' }
$labHeaders = @{ Authorization = "Bearer $env:RAG_WRITE_TOKEN" }
foreach ($id in @('fragmentacion', 'embeddings', 'persistencia')) {
  $bytes = [IO.File]::ReadAllBytes((Join-Path $labRag "eval_data/$id.txt"))
  Invoke-RestMethod -Method Put -Uri "http://127.0.0.1:$env:RAG_PORT/v1/documents/$id" -Headers $labHeaders -ContentType 'text/plain; charset=utf-8' -Body $bytes -ErrorAction Stop
}
```

Guarda el informe solo si el evaluador termina correctamente. Este bloque
captura bytes UTF-8 sin recurrir a la redirección de PowerShell, que puede
alterar la codificación según su versión. El modo `xb` evita sobrescrituras.

```powershell
$env:LAB_RAG_DIR = $labRag
$env:LAB_RUN_DIR = $labDir
@'
import json, os, pathlib, subprocess, sys
root, out = pathlib.Path(os.environ['LAB_RAG_DIR']), pathlib.Path(os.environ['LAB_RUN_DIR'])
cmd = [sys.executable, '-m', 'rag.evaluate_api', str(root / 'eval_data' / 'api_cases.json'),
       '--port', os.environ['RAG_PORT'], '--limit', '2']
result = subprocess.run(cmd, stdout=subprocess.PIPE)
if result.returncode:
    raise SystemExit(result.returncode)
json.loads(result.stdout.decode('utf-8'))
with (out / 'rag-report.json').open('xb') as stream:
    stream.write(result.stdout)
'@ | & $labPython -
if ($LASTEXITCODE -ne 0) { throw 'Falló la evaluación RAG; no continuar.' }
```

### 4. Convertir y evaluar

```powershell
$env:PYTHONPATH = Join-Path $labEvals 'src'
$env:LAB_EVALS_DIR = $labEvals
@'
import os, pathlib, subprocess, sys
root, out = pathlib.Path(os.environ['LAB_EVALS_DIR']), pathlib.Path(os.environ['LAB_RUN_DIR'])
data = root / 'eval_data' / 'rag_baseline'
cmd = [sys.executable, '-S', '-m', 'evals_lab.rag_adapter', '--report', str(out / 'rag-report.json'),
       '--corpus', str(data / 'corpus.jsonl'), '--cases', str(data / 'cases.jsonl'), '--k', '2']
result = subprocess.run(cmd, stdout=subprocess.PIPE)
if result.returncode:
    raise SystemExit(result.returncode)
with (out / 'outputs.jsonl').open('xb') as stream:
    stream.write(result.stdout)
'@ | & $labPython -S -
if ($LASTEXITCODE -ne 0) { throw 'Falló la conversión; no continuar.' }
& $labPython -S -m evals_lab.retrieval --corpus (Join-Path $labEvals 'eval_data/rag_baseline/corpus.jsonl') --cases (Join-Path $labEvals 'eval_data/rag_baseline/cases.jsonl') --outputs (Join-Path $labDir 'outputs.jsonl') --k 2
$labEvalExit = $LASTEXITCODE
if ($labEvalExit -eq 2) { throw 'La evaluación no se completó.' }
```

El código `1` de la última orden significa que existen casos fallidos o
ausentes y **no** que la conversión haya fallado. Para conservar el informe
Evals como bytes, usa el mismo patrón de captura anterior y acepta los códigos
`0` y `1`, verificando que stdout sea JSON antes de guardarlo; no guardes stdout
si retorna `2`. Registra hashes SHA-256 de los tres documentos originales,
casos de RAG, corpus y casos Evals, informe RAG y JSONL convertido, además de
los parámetros de la tabla. Los hashes identifican bytes, no prueban la
correspondencia de la base ni la calidad de las etiquetas.

### 5. Cerrar únicamente recursos de esta ejecución

Después de revisar o guardar los artefactos necesarios, detén el proceso API
identificado por `$labServer` y el contenedor **único** identificado por
`$labContainer`. El contenedor se creó con `--rm` y sin volumen nombrado; al
detenerlo se descartan sus datos efímeros. No uses `docker compose down -v` ni
elimines recursos de otros proyectos. El directorio `$labDir` contiene los
resultados y logs y permanece hasta que decidas eliminarlo. Cierra la terminal
dedicada para descartar sus variables y credenciales.

## Interpretación prevista

- Compara Hit@2 y MRR@2 solo para los tres positivos evaluados, junto con su
  cobertura; una ausencia de respuesta no equivale a lista vacía.
- Revisa el caso negativo por separado y compara la respuesta generada con los
  fragmentos. La tasa de fuentes no vacías no es una medida automática de
  alucinación ni de abstención del modelo.
- Revisa `answer` y `sources` manualmente en el informe RAG para detectar
  afirmaciones incompletas o sin sustento. El adaptador no conserva esos textos.
- Una ejecución, cuatro preguntas y etiquetas sin revisión independiente no
  permiten comparar sistemas ni extrapolar su precisión a otros corpus.
