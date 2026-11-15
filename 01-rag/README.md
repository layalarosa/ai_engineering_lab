# 01 — RAG

RAG (Retrieval-Augmented Generation) recupera fragmentos relevantes de una fuente de información y los incorpora al contexto de un modelo para responder preguntas. Separa el conocimiento documental del modelo, pero la calidad de la respuesta depende de la ingesta, la recuperación y el contexto seleccionado.

Consulta el [estado y las limitaciones actuales](docs/estado-y-pendientes.md) para una visión resumida. Las secciones posteriores conservan las etapas de aprendizaje y sus ejemplos.

## Primera etapa: fragmentación

Comenzamos con una función pura que divide texto en fragmentos de caracteres con solapamiento. El solapamiento conserva parte del contexto en los límites. Es una aproximación deliberadamente simple: los caracteres no equivalen a tokens y pueden cortar frases. Más adelante evaluaremos alternativas con documentos reales.

La función de fragmentación usa la biblioteca estándar. Para ejecutar la suite completa desde este directorio con Python 3.10 o superior, instala primero las dependencias:

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

La función recibe texto, tamaño máximo y solapamiento; devuelve una lista ordenada de fragmentos. Rechaza tamaños inválidos y devuelve una lista vacía para texto vacío.

## Arquitectura prevista

Una canalización sencilla separará ingesta, fragmentación, embeddings, almacenamiento/búsqueda y generación. Inicialmente se implementarán funciones pequeñas y contratos claros; se introducirán adaptadores para PostgreSQL/pgvector y proveedores de modelos cuando se necesiten. Cada etapa podrá probarse por separado. Ollama será la opción local y OpenAI una alternativa configurable. No se incorporan servicios ni frameworks antes de tener una necesidad verificable.

## Segunda etapa: ingesta de texto

`ingest_text_file` lee un archivo UTF-8 y aplica la fragmentación. Cada resultado contiene la ruta de origen, un índice desde cero y el texto; estos metadatos permiten rastrear y ordenar los fragmentos. La ruta identifica el archivo tal como se proporcionó, no es un identificador global estable. Un documento vacío no produce fragmentos.

```python
from rag.ingestion import ingest_text_file

chunks = ingest_text_file("documento.txt", chunk_size=500, overlap=50)
for chunk in chunks:
    print(chunk.source, chunk.index, chunk.text)
```

Para ejecutar este ejemplo, añade `src` a `PYTHONPATH`. Los errores de archivo inexistente, permisos y UTF-8 inválido se propagan al llamador; no se descartan documentos silenciosamente. La lectura carga el archivo completo en memoria y está pensada para documentos pequeños en esta etapa.

La siguiente etapa propuesta es estudiar embeddings y similitud mediante un ejemplo pequeño y comprobable, antes de conectar un proveedor de modelos y PostgreSQL.

## Tercera etapa: vectores y similitud

Un embedding es una representación numérica producida por un modelo. La similitud coseno compara la dirección de dos vectores: valores cercanos a 1 indican direcciones parecidas, 0 indica perpendicularidad y -1 direcciones opuestas. Para recuperación, la consulta y los fragmentos deben codificarse con el mismo modelo y en el mismo espacio vectorial. La similitud no garantiza que una respuesta sea correcta.

`rag.similarity.cosine_similarity` calcula el coseno y `rank_vectors` ordena candidatos por puntuación, devolviendo sus índices y puntuaciones. Rechazan dimensiones incompatibles, valores no finitos y vectores de norma cero. Ejemplo didáctico con vectores inventados, no embeddings reales:

```python
from rag.similarity import rank_vectors

print(rank_vectors([1, 0], [[0, 1], [1, 0], [0.8, 0.2]], limit=2))
```

## Cuarta etapa: embeddings reales y búsqueda en memoria

El cliente envía los fragmentos y la consulta a `POST /api/embed` de Ollama y ordena los fragmentos con similitud coseno. Envía `truncate: false` para detectar entradas que excedan el contexto, en lugar de perder texto silenciosamente. El modelo debe ser el mismo para consulta y documentos. Los vectores se calculan de nuevo en cada ejecución y se mantienen solo en memoria: no hay persistencia ni generación de respuestas todavía. Es adecuado para experimentar con documentos pequeños; una petición grande puede exceder los recursos disponibles.

Con Ollama iniciado y un modelo de embeddings instalado, desde `01-rag` en PowerShell:

```powershell
ollama pull nomic-embed-text
$env:PYTHONPATH = 'src'
python -m rag README.md '¿Qué es la similitud coseno?' --model nomic-embed-text --limit 2
```

También se puede configurar `OLLAMA_EMBED_MODEL` y, opcionalmente, `OLLAMA_BASE_URL` (por defecto `http://localhost:11434`). `--model` y `--url` sobrescriben esas variables. El programa imprime puntuaciones, texto y origen. El cliente limita el tiempo de espera a 120 segundos y comunica errores de conexión, HTTP o respuestas inválidas. El documento y la consulta se envían al servidor configurado: usa un servidor de confianza.

## Quinta etapa: persistencia con pgvector

PostgreSQL conserva los fragmentos y sus vectores entre ejecuciones. `rag_models` registra la dimensión de cada modelo y `rag_chunks` guarda texto, origen, posición y embedding. La indexación sustituye de forma transaccional los fragmentos de un mismo origen y modelo; si falla, conserva la versión anterior. Un documento vacío elimina sus fragmentos anteriores. Las rutas tal como las interpreta `Path` son la identidad del origen: usa siempre la misma ruta al reindexar.

La consulta calcula únicamente el embedding de la pregunta y usa el operador de distancia coseno `<=>`; la puntuación es `1 - distancia`. La búsqueda es exacta y no tiene índice vectorial aproximado. Se filtra por nombre de modelo y se verifica la dimensión; no reutilices un nombre de modelo para pesos distintos, porque una misma dimensión no garantiza espacios compatibles. Si actualizas el modelo, reindexa todos sus documentos bajo un nombre diferente o reinicia los datos de desarrollo.

Desde `01-rag`, con Docker Desktop iniciado y Ollama ejecutándose en el equipo:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:POSTGRES_PASSWORD = Read-Host 'Contraseña local de PostgreSQL'
docker compose up -d --wait
$env:DATABASE_URL = 'postgresql://rag:CONTRASEÑA@localhost:5432/rag'
$env:OLLAMA_EMBED_MODEL = 'nomic-embed-text'
$env:PYTHONPATH = 'src'
python -m rag.database_cli init
python -m rag.database_cli index README.md
python -m rag.database_cli search '¿Qué es la similitud coseno?' --limit 2
```

Sustituye `CONTRASEÑA` por la misma contraseña usada para iniciar la base; codifica los caracteres especiales según las reglas de URL. Las variables de entorno y la cadena de conexión contienen secretos: no las incluyas en el repositorio. La contraseña de Compose solo inicializa un volumen nuevo; cambiarla después no actualiza la cuenta existente. `docker compose down` detiene los servicios y conserva el volumen. La base se publica únicamente en localhost. Para producción se necesitan credenciales, permisos, copias de seguridad y configuración de red adecuados.

Para probar la integración, prepara una **base de pruebas dedicada** con pgvector y configura `RAG_TEST_DATABASE_URL` con su conexión; después ejecuta `python -m unittest discover -s tests -v`. La prueba crea tablas si faltan y elimina las filas de prueba que genera. Sin esta variable la prueba de integración se omite. Las demás pruebas no requieren PostgreSQL ni Ollama.

## Sexta etapa: generación de respuestas

`ask` convierte la pregunta en un embedding, recupera fragmentos de PostgreSQL y los envía como contexto a un modelo de generación en Ollama. Los modelos de embeddings y generación se configuran por separado. El prompt pide responder solo con el contexto y reconocer cuando no sea suficiente. Si no hay fragmentos, o ninguno supera el umbral opcional, se devuelve un mensaje sin llamar al modelo.

Después de inicializar la base e indexar el documento como se indicó arriba:

```powershell
$env:OLLAMA_GENERATION_MODEL = 'llama3.1:8b'
python -m rag.database_cli ask '¿Qué es la similitud coseno?' --limit 3
# Umbral opcional, que debes calibrar para tus documentos y modelo:
python -m rag.database_cli ask '¿Qué es la similitud coseno?' --min-score 0.5
```

También se admite `--generation-model`. El modelo de generación debe estar instalado en Ollama. El resultado muestra todos los fragmentos enviados, con etiquetas, ruta, posición y puntuación; son fuentes recuperadas, no citas verificadas automáticamente. Sin `--min-score` se envían los mejores resultados disponibles aunque sean poco relevantes. Un umbral de similitud no garantiza relevancia ni suficiencia: depende del modelo y del conjunto de documentos. Las instrucciones al modelo reducen, pero no eliminan, los riesgos de respuestas inventadas o instrucciones maliciosas dentro de los documentos. Revisa la respuesta frente a las fuentes. Los textos recuperados se envían al servidor Ollama configurado.

## Séptima etapa: evaluación de RAG

El directorio `eval_data` contiene tres documentos breves y cuatro preguntas: tres con una fuente esperada y una sin respuesta en el corpus. La evaluación calcula **Hit@k** (proporción de preguntas positivas cuya fuente aparece entre los primeros k fragmentos) y **MRR@k** (media del inverso de la posición de la primera coincidencia; cero cuando no aparece). Las coincidencias se comprueban por ruta de documento, no por contenido o fragmento concreto. Los casos sin fuente esperada se muestran por separado y no forman parte de esas métricas.

Usa una base de evaluación independiente, inicializada como se explicó en la quinta etapa, y ejecuta desde `01-rag`:

```powershell
$env:PYTHONPATH = 'src'
$env:OLLAMA_EMBED_MODEL = 'nomic-embed-text'
python -m rag.database_cli init
python -m rag.database_cli index eval_data/fragmentacion.txt
python -m rag.database_cli index eval_data/embeddings.txt
python -m rag.database_cli index eval_data/persistencia.txt
python -m rag.evaluate eval_data/cases.json --limit 2
```

La ruta de cada fuente esperada debe coincidir con la utilizada al indexar. Una base con otros documentos altera los resultados. Añade `--generation-model llama3.1:8b` para registrar respuestas junto a sus referencias esperadas y los fragmentos seleccionados; revisar esas respuestas es una tarea manual. `--min-score 0.5` aplica un filtro opcional antes de calcular las métricas y generar respuestas. El resultado es JSON impreso en pantalla; incluye el modelo, la configuración, las puntuaciones y el texto recuperado. Cada pregunta requiere una llamada de embeddings y, si se solicita, una de generación.

La pregunta sin respuesta permite inspeccionar si el sistema se abstiene, pero no hay una puntuación automática de abstención ni de exactitud de respuesta. Sin umbral, el buscador devolverá los vecinos más cercanos aunque sean irrelevantes. Este conjunto es didáctico y demasiado pequeño para afirmar calidad general; amplíalo con casos representativos, preguntas difíciles y documentos reales antes de comparar cambios.

## Octava etapa: ejecución reproducible y CI

La dependencia directa de PostgreSQL está fijada en `requirements.txt`; la imagen Docker instala esa versión y ejecuta la aplicación como usuario sin privilegios. Desde `01-rag`:

```powershell
docker build -t ai-engineering-lab-rag .
docker run --rm ai-engineering-lab-rag --help
```

Para ejecutar comandos que acceden a PostgreSQL u Ollama instalados en el equipo, configura en el contenedor `DATABASE_URL` y `OLLAMA_BASE_URL` usando `host.docker.internal` como nombre de host en Docker Desktop, en lugar de `localhost`. Pasa las variables con `--env DATABASE_URL --env OLLAMA_BASE_URL` y monta los archivos que desees indexar con `--mount`; la ruta del archivo dentro del contenedor será su origen almacenado. No incluyas credenciales en la imagen. El `compose.yaml` sigue levantando únicamente PostgreSQL; la aplicación se ejecuta a demanda.

El flujo de GitHub Actions ejecuta las pruebas con Python 3.10 y 3.12 y una base pgvector temporal, y construye y comprueba la imagen de la aplicación. Se activa para cambios en RAG o en el flujo y también admite ejecución manual. Las pruebas usan datos ficticios y no necesitan Ollama ni secretos de GitHub. La evaluación con modelos reales se ejecuta por separado y no bloquea CI. La versión de la dependencia directa está fijada, aunque la imagen base y dependencias transitivas no tienen bloqueo por hash; no se garantiza una compilación idéntica en el tiempo. No se publica ni despliega la imagen automáticamente.

## Novena etapa: límites y errores operativos

Para esta versión de laboratorio se rechazan documentos de más de 1.000.000 de bytes o que produzcan más de 1.000 fragmentos. La lectura es limitada antes de decodificar y fragmentar. El comando de consulta admite hasta 4.000 caracteres y entre 1 y 20 resultados; para generación, el texto total de los fragmentos seleccionados está limitado a 20.000 caracteres. Las respuestas HTTP de Ollama se limitan a 16.000.000 de bytes. Las URL del servicio deben usar HTTP o HTTPS y no incluir credenciales, parámetros de consulta ni fragmentos; el tiempo de espera debe ser positivo y finito.

Son límites sencillos, fijados en código, no límites de tokens ni garantías de que el contexto completo quepa en el modelo. Si una solicitud supera un límite se informa del error en vez de recortar silenciosamente el documento. La indexación realiza la lectura y obtiene los vectores antes de sustituir los datos en una transacción: un error en esos pasos conserva lo almacenado. En cambio, indexar un archivo **válido y vacío** elimina los fragmentos anteriores de esa ruta y modelo. Los documentos siguen tratándose como datos no confiables y un prompt no garantiza protección completa frente a instrucciones maliciosas.

## Décima etapa: proveedor OpenAI

El cliente envía peticiones HTTP a los endpoints oficiales de [embeddings](https://developers.openai.com/api/reference/resources/embeddings/methods/create) y [Responses](https://developers.openai.com/api/reference/resources/responses/methods/create). Ollama sigue siendo el proveedor predeterminado. Para usar OpenAI, configura una clave y modelos disponibles en tu cuenta; por ejemplo, desde `01-rag` con PostgreSQL inicializado:

```powershell
$env:OPENAI_API_KEY = Read-Host 'Clave de API de OpenAI'
$env:OPENAI_EMBED_MODEL = 'text-embedding-3-small'
$env:OPENAI_GENERATION_MODEL = 'gpt-4.1-mini'
python -m rag.database_cli index README.md --provider openai
python -m rag.database_cli ask '¿Qué es la similitud coseno?' --provider openai
```

También se pueden pasar `--model` y `--generation-model`. `--generation-provider ollama` permite usar embeddings de OpenAI y generación local; a la inversa, usa `--provider ollama --generation-provider openai`. Cada proveedor utiliza sus propias variables de modelo; `OLLAMA_BASE_URL` solo se aplica a Ollama. La evaluación acepta las mismas opciones de proveedor, con `--generation-model` para activar generación.

Los vectores OpenAI se guardan con el prefijo interno `openai:` y los existentes de Ollama conservan su identificador; no se comparan entre proveedores. Se debe volver a indexar con el nuevo modelo. Cambiar los pesos detrás del mismo identificador sigue requiriendo una reindexación controlada. OpenAI recibe los textos enviados para embeddings y, al generar, la pregunta y los fragmentos recuperados. Las solicitudes pueden tener coste; revisa [precios](https://platform.openai.com/pricing) y condiciones de datos antes de usar información sensible. La solicitud Responses usa `store: false`; esto no sustituye la revisión de las políticas de retención aplicables. No incluyas la clave en código, imágenes o repositorios.

Las pruebas del cliente simulan las respuestas de OpenAI y no requieren clave ni llamadas facturables. La integración real requiere credenciales y acceso al modelo.

## Undécima etapa: observabilidad

El comando de base de datos acepta `--observe` para emitir un evento JSON por etapa a la salida de errores estándar:

```powershell
python -m rag.database_cli search '¿Qué es un embedding?' --model nomic-embed-text --observe
```

Cada evento incluye hora UTC, identificador aleatorio de ejecución, nombre de etapa, estado y duración en milisegundos; las etapas que utilizan modelos indican el proveedor. El tiempo se mide con un reloj monotónico. Se observan la conexión, inicialización, ingesta, embeddings, persistencia, recuperación y preparación de la respuesta, según el comando. La etapa `answer` incluye el filtrado y puede terminar sin llamar al modelo cuando no hay contexto suficiente. Un fallo durante una etapa registra `error` y se propaga al tratamiento habitual de errores. No se emiten eventos para todos los errores de configuración previos a las etapas.

Los eventos se generan solo al activar la opción y no incluyen preguntas, documentos, rutas, modelos, URLs, credenciales ni texto de excepciones. **La salida habitual del comando sí muestra fragmentos y rutas**, y los mensajes de error habituales pueden contener detalles del entorno; controla dónde se redirigen o almacenan ambas salidas. No se registran métricas agregadas, consumo de tokens ni trazas distribuidas. El evaluador y la búsqueda en memoria todavía no emiten estos eventos.

No se expone una API en esta etapa: aún no hay un consumidor, requisitos de autenticación o contrato de integración definidos. La interfaz de línea de comandos permite seguir validando el flujo sin añadir un servicio permanente. Cuando exista un caso de uso concreto, se podrá definir el contrato y los límites de acceso antes de implementarla.

## Duodécima etapa: diseño de integración

La [propuesta de API interna](docs/api-design.md) define un caso de integración de un solo corpus, operaciones síncronas de indexación y consulta, contratos de error y condiciones de seguridad.

## Decimotercera etapa: API local mínima

La API de laboratorio escucha únicamente en `127.0.0.1:8080` por defecto; se puede cambiar el puerto con `RAG_PORT` (1–65535). Implementa `PUT /v1/documents/{document_id}` y `POST /v1/answers` según el diseño, con dos credenciales distintas para escritura y lectura. Requiere PostgreSQL con el esquema inicializado por separado mediante `python -m rag.database_cli init`, Ollama activo y modelos instalados. Desde `01-rag`, en PowerShell, configura `DATABASE_URL` como en la quinta etapa y después:

```powershell
$env:PYTHONPATH = 'src'
$env:RAG_EMBED_MODEL = 'nomic-embed-text'
$env:RAG_GENERATION_MODEL = 'llama3.1:8b'
$env:RAG_WRITE_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
$env:RAG_READ_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
python -m rag.api
```

En otra terminal, configura las mismas credenciales en variables de entorno de forma segura. Ejemplo de solicitudes con esas variables ya disponibles:

```powershell
$writeHeaders = @{ Authorization = "Bearer $env:RAG_WRITE_TOKEN" }
$readHeaders = @{ Authorization = "Bearer $env:RAG_READ_TOKEN" }
Invoke-RestMethod -Method Put -Uri http://127.0.0.1:8080/v1/documents/ejemplo -Headers $writeHeaders -ContentType 'text/plain; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes('Un embedding representa texto como un vector.'))
$body = @{ question = '¿Qué es un embedding?'; limit = 3 } | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/v1/answers -Headers $readHeaders -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

Para OpenAI configura `RAG_EMBED_PROVIDER` y/o `RAG_GENERATION_PROVIDER` como `openai`, los modelos correspondientes y `OPENAI_API_KEY`; el valor predeterminado de ambos proveedores es `ollama`. `RAG_MIN_SCORE` permite fijar opcionalmente un umbral entre -1 y 1. Los documentos de la API usan internamente el prefijo `api:` y sus consultas filtran por él; la CLI usa el mismo almacenamiento y puede acceder a esos datos, por lo que este prefijo no es un control de seguridad frente a usuarios de la base o la CLI.

La aplicación limita el cuerpo de texto a 1.000.000 de bytes, el JSON a 32.000 bytes y la concurrencia a ocho peticiones. Los eventos de etapa van a stderr sin cuerpos ni credenciales. Las credenciales deben generarse con alta entropía y mantenerse fuera de archivos compartidos o registros. Esta implementación usa el servidor HTTP básico de Python: es para desarrollo local y **no debe exponerse a internet**. TLS, límites de tasa por credencial y otras protecciones para un despliegue compartido requieren un proxy y una revisión posteriores. La inicialización del esquema y los privilegios de base también se administran fuera de la API.

## Decimocuarta etapa: verificación con servicios reales

El puerto de PostgreSQL publicado por Compose se puede cambiar con `RAG_POSTGRES_PORT` antes de ejecutar `docker compose up`; actualiza el puerto de `DATABASE_URL` para que coincida. Ambos servicios publicados por el proyecto siguen limitados a localhost. La imagen Docker existente ejecuta el CLI, mientras que la API se ejecuta en el equipo como se indicó arriba; no hay aún una imagen o despliegue de la API configurado para escucha externa.

La prueba opcional `tests/test_live_api.py` inicia la API en un puerto local temporal, indexa un documento por HTTP y solicita una respuesta utilizando PostgreSQL y Ollama reales. Usa una **base de pruebas dedicada**: la prueba crea el esquema si falta, añade un documento temporal y elimina sus fragmentos al terminar; la tabla de modelos puede conservar su registro. La prueba puede consumir tiempo y recursos del modelo. Desde `01-rag`:

```powershell
$env:RAG_TEST_DATABASE_URL = 'postgresql://rag:CONTRASEÑA@localhost:5432/rag_test'
$env:RAG_LIVE_TEST = '1'
$env:RAG_LIVE_EMBED_MODEL = 'nomic-embed-text'
$env:RAG_LIVE_GENERATION_MODEL = 'llama3.1:8b'
python -m unittest discover -s tests -p test_live_api.py -v
```

La prueba comprueba el recorrido HTTP, la presencia de respuesta y la recuperación de la fuente, no evalúa automáticamente la exactitud semántica de la respuesta. Se omite por defecto y no usa OpenAI ni una API facturable. CI sigue ejecutando las pruebas sin modelos reales. Mantén el servicio en localhost: una implementación para red compartida aún necesita un servidor HTTP adecuado, TLS, control de tasa y pruebas de carga.

## Decimoquinta etapa: evaluación a través de la API

`rag.evaluate_api` envía las preguntas de `eval_data/api_cases.json` a la API local, guarda cada respuesta y sus fuentes en un informe JSON y calcula Hit@k y MRR@k por identificador de documento para los casos con respuesta conocida. El campo `negative_no_context` cuenta los casos sin respuesta esperada en los que la API devolvió `no_context`: no mide todas las abstenciones del modelo, que puede responder `answered` y explicar que desconoce la respuesta. El evaluador no califica automáticamente si una respuesta es correcta o está sustentada; compara manualmente `answer` con `expected_answer` y las fuentes.

Con la API iniciada, una **base de evaluación dedicada y un corpus API vacío**, indexa primero los tres documentos de ejemplo desde `01-rag`:

```powershell
$writeHeaders = @{ Authorization = "Bearer $env:RAG_WRITE_TOKEN" }
foreach ($id in @('fragmentacion', 'embeddings', 'persistencia')) {
  $bytes = [IO.File]::ReadAllBytes("eval_data/$id.txt")
  Invoke-RestMethod -Method Put -Uri "http://127.0.0.1:8080/v1/documents/$id" -Headers $writeHeaders -ContentType 'text/plain; charset=utf-8' -Body $bytes
}
python -m rag.evaluate_api eval_data/api_cases.json --limit 2
```

`RAG_READ_TOKEN` debe estar disponible en la terminal que ejecuta el evaluador. Usa `--port` si la API escucha en otro puerto local. El informe incluye preguntas, respuestas y fragmentos, y puede contener información sensible si sustituyes el corpus; protege cualquier copia que guardes. Si una petición falla o la respuesta no cumple el contrato, el proceso termina con error y no calcula métricas parciales. Las preguntas sin respuesta requieren revisión humana incluso si se configura un umbral. Los cuatro casos son didácticos y no permiten concluir que el sistema sea robusto en general.

Para repetir la evaluación completa de este corpus con Ollama y PostgreSQL reales en una base de pruebas dedicada, usa las variables de la etapa anterior y ejecuta `python -m unittest discover -s tests -p test_live_evaluation.py -v`. La prueba exige que no existan fragmentos API para el modelo en esa base, indexa los tres documentos y los elimina al terminar; el esquema y el registro del modelo pueden permanecer. Esta prueba y el evaluador pueden consumir tiempo y recursos de los modelos.

## Decimosexta etapa: contexto irrelevante y sustento

`eval_data/calibration_cases.json` amplía la muestra con seis preguntas respondibles y cuatro sin respuesta, incluidas preguntas cercanas al contenido. `python -m rag.calibrate --model nomic-embed-text` compara cinco umbrales con los mismos embeddings y muestra qué fuentes se conservan; requiere Ollama, pero no PostgreSQL ni generación. Ejecuta desde `01-rag` con `PYTHONPATH=src`. El experimento usa búsqueda exacta en memoria, un solo fragmento por documento y los tres documentos de ejemplo.

Los [resultados observados y la guía de revisión humana](eval_data/calibration_findings.md) muestran que un umbral alto elimina contexto válido y aun así puede conservar fragmentos que no responden una pregunta. No se cambia el umbral de la API ni se incorpora un verificador automático: la fidelidad requiere comparar las afirmaciones con la evidencia. Antes de modificar el comportamiento, conviene ampliar los datos, separar calibración de evaluación y medir el efecto en preguntas reales.

## Decimoséptima etapa: comparación de recuperación

La evaluación incorpora un conjunto de preguntas [reservado](eval_data/holdout_cases.json), distinto del conjunto de calibración. `python -m rag.compare_retrieval --model nomic-embed-text` compara la búsqueda por coseno con una mezcla experimental de coseno y coincidencia exacta de palabras, con peso léxico fijo de 0,2. Ambos métodos ordenan exactamente los mismos documentos usando los mismos embeddings. Se ejecuta desde `01-rag` con `PYTHONPATH=src` y Ollama activo; no modifica PostgreSQL ni la API.

La [comparación observada](eval_data/retrieval_comparison.md) no mostró mejora en Hit@1, Hit@2 ni MRR para los casos con fuente esperada, por lo que la API conserva su búsqueda actual. Los casos sin respuesta se cuentan por separado: una clasificación sin umbral siempre devuelve documentos y no demuestra que el contenido responda la pregunta. El corpus sigue siendo pequeño y compartido entre conjuntos; deben incorporarse consultas y documentos reales antes de sacar conclusiones más amplias.

## Decimoctava etapa: robustez ante fallos

Las pruebas controladas bloquean dos solicitudes de forma simultánea, verifican el rechazo de una tercera al agotarse los cupos y comprueban que la API vuelve a atender peticiones cuando se liberan. También simulan errores y tiempos de espera del proveedor y de la base, y verifican que los mensajes públicos no incluyan los detalles internos del fallo. Con PostgreSQL real se comprueba que una sustitución fallida, incluida una que falla después de iniciar la transacción, conserve el documento anterior.

Al saturarse, el servidor intenta devolver HTTP 503; según el sistema operativo, el cliente también puede experimentar un cierre de conexión porque el rechazo ocurre antes de leer el cuerpo de la solicitud. Los clientes deben tratar ambos casos como fallos transitorios y limitar los reintentos. Un proxy o servidor de producción debe encargarse del control de carga y de respuestas consistentes. Las pruebas simulan la lentitud y los fallos: no son una prueba de carga, ni verifican plazos de extremo a extremo, TLS, recuperación tras caída del proceso o comportamiento ante clientes hostiles. El servidor continúa siendo solo para desarrollo local.

## Decimonovena etapa: diseño de ejecución compartida

La [propuesta de despliegue compartido](docs/shared-deployment-design.md) define un caso de uso interno, proxy TLS, adaptación de la API a un servidor ASGI, límites iniciales para experimentación y un plan de pruebas de carga y fallos. Los límites propuestos requieren medición y no son garantías de capacidad.

## Vigésima etapa: adaptación ASGI y proxy de laboratorio

`rag.asgi` adapta las operaciones existentes a ASGI y las ejecuta en un máximo de cuatro hilos. Si el trabajo síncrono sigue en curso después de una cancelación, conserva su cupo hasta terminar; el límite es por proceso, no global entre réplicas. El servidor local anterior se conserva. Esta adaptación reutiliza la lógica del controlador HTTP anterior y aún no constituye una extracción completa de las capas de transporte y servicio.

La carpeta `tests/proxy_fixture` contiene un entorno **solo para pruebas** con Uvicorn, Nginx, PostgreSQL temporal y un servidor de modelos simulado. Nginx publica HTTPS únicamente en `127.0.0.1`, aplica límites de tamaño y una tasa de prueba por IP, y no registra cuerpos ni cabeceras de autorización en su registro de acceso. Su red interna no publica los puertos de la API, la base ni el modelo. Las credenciales fijas y el certificado autofirmado de corta duración son exclusivos de la prueba; no usarlos para servicios compartidos.

Con Docker Desktop, OpenSSL y el entorno virtual instalado, desde `01-rag` en PowerShell:

```powershell
.\tests\proxy_fixture\run.ps1
# Opcional: escoger otro puerto local
.\tests\proxy_fixture\run.ps1 -Port 18444
```

El guion crea un proyecto Compose con nombre único y un certificado temporal, verifica TLS, autorización, indexación, consulta y rechazo por tasa, y elimina los contenedores, volúmenes y certificado de esa ejecución. La prueba usa un proveedor de modelos simulado, no llama a Ollama ni OpenAI. El certificado correcto se valida y se rechazan uno no confiable y un nombre de servidor distinto.

Esto **no es un despliegue de producción**: no se han configurado certificados de una autoridad interna, permisos mínimos de base, gestión o rotación de secretos, un presupuesto total de solicitud ni pruebas de carga sostenida. El límite de Nginx se aplica por IP, por lo que clientes detrás de un NAT comparten cuota. Los errores del proxy no necesariamente tienen el mismo formato o identificador que los de la aplicación. La prueba de integración del proxy es manual y no se ejecuta en GitHub Actions; las pruebas unitarias ASGI sí forman parte de la suite habitual.

## Vigésima primera etapa: ráfagas y fallos aislados

El perfil opcional de carga usa un proveedor simulado con demora fija, datos sintéticos y una tasa de proxy más alta exclusivamente para observar la concurrencia. El guion envía grupos de 1, 2, 4 y 6 peticiones simultáneas y detiene y reinicia por separado el modelo simulado y la base del proyecto temporal. Comprueba que, tras cada reinicio, la respuesta vuelva a incluir el documento de prueba. No usa Ollama real, OpenAI ni servicios externos.

```powershell
.\tests\proxy_fixture\run.ps1 -Load
```

El informe muestra estados, tiempos y el tamaño de muestra; p95 y p99 se omiten cuando hay menos de 20 y 100 respuestas exitosas respectivamente. La [ejecución observada](tests/proxy_fixture/load_findings.md) mostró rechazos por el límite de dos peticiones por IP y recuperación de ambos servicios simulados. Es una prueba breve y no determina capacidad sostenida ni un SLO; el perfil de carga no debe utilizarse como configuración de despliegue compartido.

## Vigésima segunda etapa: carga breve con varias IP y timeout

Los siguientes modos crean un proyecto Compose temporal, usan un certificado local de prueba y eliminan sus contenedores y volúmenes al terminar:

```powershell
.\tests\proxy_fixture\run.ps1 -Sustained
.\tests\proxy_fixture\run.ps1 -Timeout
```

`-Sustained` inicia tres clientes en contenedores separados durante una ventana de 12 segundos, con dos hilos cada uno. Los clientes se conectan únicamente al proxy del entorno de prueba; el proveedor es simulado. El informe separa 200, 429 y 503, calcula latencias de respuestas exitosas y muestrea CPU y memoria con `docker stats`. La tasa de este perfil se eleva solo para el experimento. `-Timeout` usa otro perfil: un proveedor simulado lento y un plazo de lectura de dos segundos en el proxy. Comprueba el 504 y una operación posterior; no demuestra la cancelación del trabajo subyacente.

Los [resultados observados](tests/proxy_fixture/sustained_findings.md) describen las condiciones y sus límites. Es una prueba sostenida **breve**, con datos sintéticos y sin modelo real; no define capacidad, estabilidad prolongada ni un SLO de producción. Los scripts no forman parte de CI y no ejecutan carga contra OpenAI ni otros servicios externos.

## Vigésima tercera etapa: permisos, secretos y restauración

La [guía de operaciones de base de datos](docs/database-operations.md) describe la separación entre administración y el rol de ejecución, secretos montados mediante variables `_FILE` y la prueba de copia y restauración en bases temporales. La política SQL se limita a las tablas de RAG y requiere revisión antes de utilizarla fuera de una base dedicada. La prueba no crea un sistema de copias de producción ni modifica bases existentes.

## Vigésima cuarta etapa: verificación en CI y revisión

El flujo de CI incluye la prueba portable de permisos y restauración en un contenedor nuevo, además de las pruebas de la aplicación. El mismo verificador puede ejecutarse localmente con `python tests/postgres_fixture/run.py` desde `01-rag`, con Docker activo y las dependencias instaladas. No utiliza una base existente ni requiere un modelo. La [revisión del estado](docs/estado-y-pendientes.md) recoge el alcance y los puntos aún abiertos antes de un uso compartido.
