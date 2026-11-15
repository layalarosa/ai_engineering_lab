# 02 — MCP: fundamentos y diseño

**Estado: versión local implementada.** Incluye un servidor stdio didáctico, conexión opcional a RAG, un cliente de demostración y opciones de empaquetado. Se ha contrastado un conjunto limitado de operaciones con el SDK oficial de Python; no se afirma compatibilidad completa con MCP.

## Qué problema resuelve MCP

Model Context Protocol (MCP) define una forma común de conectar una aplicación de IA con herramientas y fuentes de contexto externas. En vez de que cada aplicación invente un contrato distinto para descubrir e invocar capacidades, un cliente MCP intercambia mensajes con servidores que anuncian las suyas. La especificación utiliza JSON-RPC 2.0; un mensaje de petición tiene un identificador para relacionarlo con su respuesta, mientras que una notificación no espera respuesta. [Visión general de MCP](https://modelcontextprotocol.io/specification/2025-11-25)

Los participantes tienen responsabilidades distintas:

- **Host:** la aplicación que integra el modelo y administra las conexiones y decisiones de interacción.
- **Cliente MCP:** el componente del host que habla el protocolo con un servidor.
- **Servidor MCP:** el proceso que publica capacidades, como herramientas, recursos o plantillas de prompts.

MCP no ejecuta el modelo por sí mismo ni convierte una herramienta en segura automáticamente. El host decide cómo presentar y autorizar su uso; el servidor debe validar los argumentos y restringir lo que expone.

## Primera versión

El servidor local en Python comenzó con una herramienta pura, `text_stats`. Recibe `text`, una cadena de hasta 4.000 puntos de código Unicode, y devuelve tres valores: número de puntos de código (`characters`), bytes UTF-8 (`utf8_bytes`) y palabras separadas por espacios en blanco (`words`). El texto vacío es válido. Esta herramienta no acepta campos adicionales, no accede a archivos ni a la red y no necesita un modelo. Así podemos observar el protocolo sin mezclarlo con permisos de acceso a servicios.

Ejemplo conceptual de la herramienta: para `"Hola mundo"`, el resultado sería `{"characters": 10, "utf8_bytes": 10, "words": 2}`. Los puntos de código no siempre equivalen a caracteres percibidos ni a tokens; el conteo de palabras será deliberadamente simple y estará documentado y probado.

La capacidad inicial será únicamente `tools`: el cliente podrá descubrir la definición con `tools/list` e invocarla con `tools/call`. Los argumentos se describirán con JSON Schema. El resultado se entregará como contenido textual MCP con JSON serializado. Los errores de protocolo y los errores de ejecución de la herramienta se distinguirán según la [especificación de herramientas](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#Error-Handling).

## Versión y transporte

Para la primera implementación didáctica se propone fijar **MCP `2025-11-25`**, de modo que podamos estudiar explícitamente `initialize`, la negociación de capacidades y la notificación `notifications/initialized`. Esta es una decisión de alcance, no una afirmación de compatibilidad con cualquier cliente. La [versión más reciente de la especificación](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning) describe un modelo distinto, con versión y capacidades en cada petición y sin ese intercambio inicial obligatorio; su compatibilidad se abordará y probará en otra etapa. Para la versión seleccionada, si el cliente propone otra versión, el servidor responde con una versión que soporta y el cliente decide si puede continuar. [Ciclo de vida de MCP `2025-11-25`](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)

Usaremos **stdio**: el cliente inicia un proceso hijo y ambos intercambian mensajes JSON-RPC UTF-8 delimitados por salto de línea. `stdout` queda reservado a mensajes del protocolo; diagnósticos van a `stderr`. Esto permite estudiar el intercambio local sin abrir un puerto. No es un servidor compartido ni una implementación de Streamable HTTP. [Transporte stdio](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#stdio)

El flujo esperado será:

```text
Cliente                          Servidor
  | -------- initialize ---------> |
  | <------- capacidades --------- |
  | -- notifications/initialized ->|
  | -------- tools/list ---------> |
  | <---- definición y esquema --- |
  | -------- tools/call ---------> |
  | <---------- resultado -------- |
```

## Organización

```text
02-mcp/
  README.md
  src/mcp_lab/
    protocol.py       # validación y despacho JSON-RPC/MCP
    server.py         # lectura y escritura stdio
    tools.py          # función text_stats y definición de la herramienta
    rag_client.py     # herramienta ask_rag y cliente HTTP local
    config.py         # configuración de RAG y lectura del token
    supervisor.py     # plazo total y supervisión del proceso auxiliar
    rag_worker.py     # consulta HTTP aislada mediante un canal privado
  src/__main__.py     # punto de entrada del paquete ejecutable
  build.py            # empaquetado zipapp sin dependencias externas
  Dockerfile          # imagen stdio con usuario sin privilegios
  demo.py             # cliente que inicia el servidor como subprocess
  tests/              # pruebas de funciones y del proceso stdio
```

Una implementación pequeña con biblioteca estándar permitirá inspeccionar el ciclo de vida y el formato de los mensajes. Será un **subconjunto explícito** de la versión seleccionada: no se afirmará compatibilidad completa con MCP hasta contrastarla con la especificación y clientes reales. Se valorará incorporar el SDK oficial después de comprender el intercambio y cuando aporte validación, compatibilidad o transportes que justifiquen la dependencia. No se introducen Clean Architecture, una base de datos ni una API web para esta función pura.

## Ejecución y pruebas

Desde `02-mcp`, con Python 3.10 o superior; no se requieren dependencias externas:

```powershell
python demo.py
python -m unittest discover -s tests -v
```

El cliente de demostración inicia el servidor, envía la inicialización, la notificación, el descubrimiento y la llamada, y muestra las respuestas. Para iniciarlo desde otro cliente, ejecuta `python -m mcp_lab.server` con `src` en `PYTHONPATH` y comunica mediante sus flujos estándar. No envíes texto libre ni instrucciones de terminal por `stdin` del servidor: ese flujo es el canal del protocolo.

Se prueban Unicode, límites de la herramienta, errores, inicialización, IDs, `stdout` y cierre del proceso. JSON inválido, UTF-8 inválido y claves JSON duplicadas producen error de análisis; un objeto o ID de petición inválido produce error de protocolo. Una herramienta desconocida produce error JSON-RPC; argumentos inválidos para `text_stats` producen un resultado con `isError: true`. Los mensajes con `NaN` o `Infinity` no se aceptan como JSON. No se registran los contenidos recibidos.

Cada línea admite hasta 32.768 bytes **incluyendo el salto de línea**. Una línea demasiado grande o incompleta al cerrar `stdin` termina la sesión con un diagnóstico genérico en `stderr`, sin respuesta para esa línea. Los IDs admitidos son cadenas de hasta 256 caracteres o enteros con signo de 64 bits; no se acepta `null` ni un ID reutilizado. Se admiten como máximo 10.000 peticiones con ID y 100 llamadas a herramientas por sesión. Es un presupuesto por proceso, no un límite de tasa por tiempo ni un control para un servicio compartido.

## Alcance y compatibilidad

El procesamiento es síncrono y secuencial. Se implementan `initialize`, `notifications/initialized`, `ping`, `tools/list` y `tools/call`; no se implementan recursos, prompts, transporte HTTP, cancelación de trabajo en curso ni la revisión moderna de `2026-07-28`. La herramienta `ask_rag` accede a la API local y puede invocar un modelo externo según la configuración de RAG.

## Interoperabilidad con el SDK oficial

La prueba opcional en `interop/` usa el [SDK oficial de Python](https://github.com/modelcontextprotocol/python-sdk) como cliente stdio y mantiene el servidor implementado a mano, sin dependencias externas. Se fijó **`mcp==2.3.0`** para que esta comparación sea repetible a nivel de versión directa; las dependencias transitivas no están bloqueadas. Desde `02-mcp`, en PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-interop.txt
.\.venv\Scripts\python.exe -m unittest discover -s interop -v
```

En la ejecución local, el cliente oficial 2.3.0 negoció `2025-11-25` tanto en modo `legacy` como en `auto`; descubrió las herramientas, comprobó su esquema, obtuvo estadísticas, llamó a una API RAG simulada y recibió los errores esperados. Se verificó además que el presupuesto de 100 llamadas se reinicia al iniciar un nuevo proceso. La [documentación del SDK sobre versiones](https://py.sdk.modelcontextprotocol.io/protocol-versions/) describe la negociación automática y el modo clásico.

Al fijar el cliente directamente a `2026-07-28`, el servidor responde `Server not initialized` con código `-32002`: no implementa la detección ni la semántica modernas, ni el error moderno de versión no soportada. El éxito de estas operaciones en un SDK y transporte concretos **no certifica conformidad integral**, compatibilidad con otros clientes ni con otros transportes. La ejecución de interoperabilidad es independiente de la suite estándar en `tests/` y está incluida en el flujo CI.

El SDK aporta gestión del transporte, negociación, tipos y soporte de más funcionalidades. Para esta integración educativa conservamos la implementación manual y el SDK como dependencia de prueba; eso requiere seguir manteniendo explícitamente los límites del protocolo. Antes de ampliar el protocolo o introducir concurrencia, conviene reevaluar su adopción y contrastar seguridad y compatibilidad.

## Consultar RAG con MCP

`ask_rag` recibe `question` y `limit` opcional, y devuelve como texto JSON la respuesta y sus fuentes. Se anuncia aunque no esté configurada: en ese caso devuelve un error controlado y `text_stats` sigue funcionando. El diseño y sus límites están en [consulta RAG desde MCP](docs/rag-tool-design.md).

Primero inicia la API y sus dependencias siguiendo las instrucciones de [01-rag](../01-rag/README.md); indexa documentos antes de consultar. Desde una terminal PowerShell que tenga disponible el mismo token de lectura utilizado por la API, y situada en `02-mcp`:

```powershell
$env:MCP_RAG_BASE_URL = "http://127.0.0.1:8080"
$env:MCP_RAG_READ_TOKEN = $env:RAG_READ_TOKEN
python demo.py --ask "Que es un embedding?" --limit 3
```

Si la API se inició en otra terminal, configura el mismo token de lectura de forma segura en esta terminal. Como alternativa, asigna a `MCP_RAG_READ_TOKEN_FILE` la ruta a un archivo UTF-8 que contenga ese token y deja sin definir `MCP_RAG_READ_TOKEN`; ambas opciones son excluyentes. El archivo se vuelve a leer en cada llamada. Nunca guardes el token en el repositorio. No uses el token de escritura.

Solo se admite HTTP hacia `127.0.0.1` y puerto explícito u 80; no se siguen redirecciones ni se usan proxies. La salida puede contener datos sensibles o instrucciones no confiables de documentos. Una consulta puede generar coste y enviar información al proveedor configurado en RAG. Los fallos no se reintentan automáticamente.

`ask_rag` tiene un **plazo total local de 270 segundos** por defecto. `MCP_RAG_TOTAL_TIMEOUT_SECONDS` permite elegir un entero de 1 a 600, escrito con hasta tres dígitos ASCII; un valor inválido produce un error de herramienta. El plazo comienza después de leer y analizar el mensaje MCP. Un proceso auxiliar ejecuta la consulta y el servidor lo supervisa; si vence el plazo, intenta terminarlo y devuelve un error. El socket mantiene además un timeout de inactividad de 270 segundos, que podría vencer antes si se configura un plazo total mayor.

Por ejemplo, para fijar tres minutos en PowerShell antes de iniciar el servidor o el cliente de demostración:

```powershell
$env:MCP_RAG_TOTAL_TIMEOUT_SECONDS = '180'
```

El plazo no es una garantía de tiempo real: la creación y limpieza del proceso y la escritura al cliente pueden añadir demora. Configura el timeout del cliente MCP con margen; el cliente de demostración espera hasta 630 segundos por el proceso completo. El servidor no procesa otras solicitudes ni cancelaciones mientras espera. Terminar la espera local **no garantiza cancelar RAG ni el modelo**, que podrían seguir trabajando. Los presupuestos son por sesión, no una protección global. Consulta el [diseño y sus límites](docs/deadline-design.md).

## Prueba con servicios reales

La prueba opcional inicia la API RAG y utiliza el cliente MCP oficial para consultar un documento sintético indexado en PostgreSQL con embeddings y generación de Ollama reales. Verifica que se obtiene una respuesta no vacía y que se recupera el fragmento del documento de prueba; no mide la exactitud general ni valida automáticamente que la respuesta esté sustentada.

En Windows, con Docker Desktop y Ollama iniciados, la imagen `pgvector/pgvector:0.8.0-pg16` disponible, los modelos `nomic-embed-text` y `llama3.1:8b` instalados, y los entornos virtuales preparados según los README de ambos proyectos, ejecuta desde `02-mcp`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\interop\run_live.ps1
```

El ejecutor crea un contenedor PostgreSQL exclusivo con almacenamiento temporal en memoria, genera credenciales para la prueba y lo detiene al finalizar. No usa volúmenes PostgreSQL existentes. La API RAG escucha en un puerto local temporal y también se detiene al terminar. Para usar otros modelos ya instalados, configura `RAG_LIVE_EMBED_MODEL` y `RAG_LIVE_GENERATION_MODEL` antes de iniciar el ejecutor. La prueba puede tardar por la carga y ejecución de los modelos. El ejecutor debe lanzarse como proceso PowerShell independiente, como en el ejemplo, para no modificar las variables de la terminal actual.

También se puede ejecutar `test_live_rag.py` directamente con `MCP_LIVE_TEST=1`, `RAG_TEST_DATABASE_URL` apuntando a una **base de pruebas dedicada** y `RAG_PYTHON` con la ruta al intérprete de `01-rag`. En ese caso se inicializa el esquema, se elimina el documento de prueba al terminar y puede quedar un registro del modelo en la base. La ejecución normal de la suite omite esta prueba si no se habilita expresamente.

En la comprobación local del 15 de noviembre de 2026, el modelo respondió que el color era violeta y la fuente correspondió al documento temporal. Es una comprobación de conectividad y recorrido completo, no una evaluación de calidad.

## Prueba de red compartida en contenedores

La prueba opcional de [red compartida entre MCP y RAG](docs/container-network-design.md) ejecuta la API RAG real con PostgreSQL/pgvector y un modelo simulado dentro de una red de laboratorio. El SDK oficial inicia MCP con `docker run -i` en la red del contenedor de la API; la API continúa escuchando exclusivamente en `127.0.0.1` y no publica puertos.

Con Docker Desktop iniciado, imágenes base disponibles y el entorno virtual de MCP preparado con `requirements-interop.txt`, desde `02-mcp` en PowerShell:

```powershell
.\.venv\Scripts\python.exe .\interop\container_fixture\run.py
```

En un host Linux con Docker Engine y el mismo entorno virtual, el intérprete suele ser `.venv/bin/python`. La construcción de las imágenes puede necesitar acceso a las dependencias de `01-rag` si no están en la caché. El ejecutor usa un proyecto Compose con identificador aleatorio, una red interna y almacenamiento PostgreSQL temporal en memoria. Genera archivos con tokens de prueba y los monta de solo lectura. Al finalizar normalmente o ante fallos que permiten ejecutar la limpieza, elimina los contenedores, las etiquetas de imagen creadas y los archivos temporales; una terminación forzada puede impedirla. La caché de Docker y las imágenes base pueden permanecer. La protección de los archivos en el host depende de sus permisos y ACL; no uses esta prueba para gestionar secretos reales.

Se comprueban corpus vacío, indexación autorizada, respuesta y fuente, separación de permisos de lectura y escritura, red independiente, límite de pregunta, ausencia de contenido sensible en registros, parada y recreación de la API, y rotación coordinada del token. También se verifica que preguntas de 4.000 emojis y 4.000 caracteres de control atraviesen la API y lleguen íntegras al proveedor simulado. El modelo cuenta las llamadas: en la ejecución comprobada se observaron seis solicitudes de embeddings y dos de generación, sin repeticiones en los casos ensayados. Se verificaron los montajes de solo lectura y los usuarios no privilegiados en Docker Desktop para Windows; no se ha verificado el host Linux independiente. La disponibilidad de un puerto no demuestra que la base o el modelo estén sanos.

Esta prueba también está configurada en CI para la variante de Python 3.12. Usa una respuesta simulada, por lo que no evalúa la calidad de un modelo ni demuestra la conectividad con Ollama real en esta topología. Sigue siendo un laboratorio local: compartir la red da a MCP acceso a las rutas de red de RAG y no cifra el tráfico de loopback.

## Prueba en contenedores con Ollama real

Para probar el mismo recorrido con el Ollama instalado en el equipo, el laboratorio añade temporalmente un intermediario Nginx. RAG, MCP y PostgreSQL permanecen en la red interna; solo el intermediario se conecta además a una red Docker con salida y accede a `host.docker.internal:11434`. MCP sigue compartiendo la red de la API y la consulta de RAG permanece en `127.0.0.1`.

```text
Cliente MCP → MCP / API RAG → red interna → intermediario → Ollama del equipo
                         └──→ PostgreSQL
```

En Docker Desktop para Windows, con Ollama y Docker iniciados, los modelos locales `nomic-embed-text` y `llama3.1:8b` disponibles y el entorno virtual MCP preparado, ejecuta desde `02-mcp`:

```powershell
.\.venv\Scripts\python.exe .\interop\container_fixture\run_real.py
```

Opcionalmente, `RAG_LIVE_EMBED_MODEL` y `RAG_LIVE_GENERATION_MODEL` seleccionan otros modelos **ya instalados**. La prueba no descarga modelos. Su documento y pregunta son sintéticos, y la base y las credenciales son temporales. El ejecutor crea un proyecto Docker único y elimina sus contenedores, redes, etiquetas de imagen y archivos temporales al terminar; pueden quedar la caché de compilación y las imágenes base. Puede consumir memoria y tardar mientras Ollama carga los modelos. Es una prueba manual, fuera de CI.

El intermediario no publica puertos y acepta solo `GET /api/tags` y `POST /api/embed` o `/api/generate`; rechaza otras rutas, métodos y cadenas de consulta. Las solicitudes se dirigen a un destino fijo, sin reenviar las cabeceras del cliente, con un máximo de 1 MiB por cuerpo. Los límites de respuesta de RAG siguen aplicándose. La conexión es HTTP local sin cifrado y el límite de rutas **no es una barrera de seguridad ante un intermediario o daemon Docker comprometido**: su red adicional permite salida de red. Se confía en el equipo y en sus contenedores, y la configuración no está diseñada para producción.

La prueba comprueba que la API no alcanza directamente el host desde su red interna, que el intermediario rechaza rutas no permitidas, que los modelos están disponibles y que la respuesta incluye la fuente del documento. En la ejecución local del 15 de noviembre de 2026, `llama3.1:8b` respondió que el color era violeta. La respuesta no se valida automáticamente por su contenido ni demuestra calidad general.

En este equipo se observó que un contenedor con salida de red puede acceder al Ollama del host mediante `host.docker.internal` incluso cuando Ollama escucha en `127.0.0.1`; esa conectividad depende del entorno y de Docker Desktop. No se cambiaron la dirección de escucha ni la configuración de Ollama. La [documentación de Docker Desktop](https://docs.docker.com/desktop/features/networking/networking-how-tos/) describe el nombre utilizado para acceder al host.

## Paquete ejecutable, Docker y CI

El paquete `.pyz` reúne los módulos Python del servidor en un archivo ejecutable mediante Python 3.10 o superior. Se construye con la biblioteca estándar, sin instalar dependencias ni incluir credenciales, entornos virtuales o pruebas. El servidor sigue usando stdio; se inicia como proceso hijo de un cliente MCP:

```powershell
python build.py
python tests/artifact_smoke.py --zipapp dist/mcp_lab.pyz
# Comando para configurar en un cliente MCP:
python dist/mcp_lab.pyz
```

El último comando espera mensajes MCP en su entrada estándar; no es una consola para texto libre. Las variables opcionales de RAG se configuran en el entorno del proceso, igual que al ejecutar el servidor desde el código fuente. El archivo se crea en `dist/`, que está excluido de Git. Este es un paquete zipapp para ejecutar, no un paquete instalable con `pip`. El proceso conserva el código de salida del servidor, incluso si recibe una línea incompleta.

La imagen Docker construye ese mismo paquete y lo ejecuta con un usuario sin privilegios. Desde `02-mcp`:

```powershell
docker build -t ai-engineering-lab-mcp:local .
python tests/artifact_smoke.py --image ai-engineering-lab-mcp:local
# Comando para configurar en un cliente MCP:
docker run --rm -i ai-engineering-lab-mcp:local
```

Se usa `-i` para mantener abierto stdin y no se asigna una terminal interactiva (`-t`), porque el transporte requiere líneas JSON sin transformar. La imagen no contiene el SDK ni secretos. `text_stats` funciona sin configuración adicional. **La API RAG del equipo no queda accesible automáticamente desde esta imagen**: la dirección `127.0.0.1` de un contenedor apunta a su propia red y `ask_rag` solo acepta esa dirección. Para usar la API RAG local se puede ejecutar el paquete en el equipo. La prueba descrita arriba muestra otra opción limitada a laboratorio, compartiendo la red del contenedor de la API; no habilita destinos remotos arbitrarios.

No se añade un Compose para MCP en esta etapa: el cliente MCP inicia y administra el proceso stdio, y aún no hay una topología de servicios compartidos que orquestar. El Compose independiente de `01-rag` conserva su propia función para PostgreSQL.

El flujo [MCP CI](../.github/workflows/mcp-ci.yml) está configurado para ejecutarse ante cambios en `01-rag`, `02-mcp` o el propio flujo, y también manualmente. Ejecuta las pruebas estándar, la interoperabilidad con el SDK y la comprobación del paquete con Python 3.10 y 3.12. Con Python 3.12 también construye la imagen MCP y ejecuta la prueba de contrato con la **API RAG real, PostgreSQL/pgvector y el modelo simulado**. Esta prueba comprueba, entre otros casos, `no_context`, respuestas con fuentes, Unicode en el límite y permisos. Usa credenciales temporales de prueba; no necesita secretos de GitHub ni Ollama. Construir las imágenes puede requerir descargar imágenes y dependencias si no están en caché.

La prueba con Ollama real permanece manual y se omite en CI. Se fija la versión directa del SDK de prueba, pero sus dependencias transitivas y las imágenes base Docker no están fijadas por hash, por lo que no se promete una construcción idéntica en el tiempo. El flujo no publica imágenes ni despliega servicios. **Todavía no se ha observado una ejecución en GitHub Actions**; para que GitHub lo ejecute, el flujo y los archivos de ambos proyectos deben estar incorporados al repositorio remoto.

## Etapas posteriores

La [revisión de estado y pendientes](docs/estado-y-pendientes.md) recoge los resultados, la corrección del fallo Unicode y el plazo total implementado. Siguen pendientes la cancelación interactiva y la comprobación automática del contrato entre proyectos.

Antes de cada etapa se revisarán los resultados y se solicitará aprobación para continuar.

