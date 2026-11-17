# Diseño: medir latencia y fallos de una API RAG local

**Estado: cliente implementado y probado con un servidor local simulado.** Se
realizó después una [medición local controlada con cuatro preguntas](../eval_data/rag_baseline/live-20261117T003007Z-11504eed/README.md).
No es una prueba de carga y se conservan los evaluadores de calidad existentes.

## Qué mediremos

Para cada intento, el cliente medirá el tiempo transcurrido desde que comienza
a crear la conexión HTTP hasta que termina de leer y validar la respuesta o
detecta un error. Incluye conexión TCP, envío, espera, lectura acotada y
validación mínima del cuerpo; excluye la preparación previa del JSON de
solicitud, el cierre de la conexión y la escritura del informe. Se abrirá una
conexión nueva por intento. No se mide tiempo hasta el primer token: la API
actual devuelve una respuesta completa y no ofrece streaming en este contrato.

Se propone usar `time.monotonic_ns()` para registrar diferencias de tiempo en
nanosegundos enteros; el reloj monotónico no retrocede y su punto de referencia
no tiene significado absoluto ([Python `time`](https://docs.python.org/3.12/library/time.html#time.monotonic)).
Para mostrar milisegundos se divide por 1.000.000. La resolución real depende
del sistema, por lo que los nanosegundos no implican precisión equivalente.

La API de `01-rag` ya emite eventos de `embed`, `retrieve` y `answer`, con un
identificador de solicitud. Esos tiempos son **por etapa en el servidor**;
la duración medida aquí es de extremo a extremo desde el cliente. No deben
presentarse como la misma medida ni asumirse que su diferencia representa solo
red. La ejecución anterior con cuatro preguntas no constituye por sí sola un
benchmark de latencia: no se fijó este protocolo ni se guardaron muestras de
cliente comparables.

## Alcance y límites del MVP propuesto

- Solo `POST /v1/answers` en `127.0.0.1` con puerto explícito. Sin URLs
  arbitrarias, redirecciones, indexación ni acceso directo a proveedores. El
  token de lectura se obtiene de `RAG_READ_TOKEN`, se valida como ASCII
  URL-safe de 32 a 128 caracteres y nunca se imprime o guarda.
- Casos JSONL sintéticos `{id,input}`: de 1 a 30 casos, IDs únicos según el
  contrato de Evals y preguntas UTF-8 no vacías, de hasta 4.000 puntos de código.
  Un archivo de hasta 2 MiB y líneas de hasta 65.536 bytes sin LF o CRLF.
- Repeticiones enteras de 1 a 5, con un máximo de **30 intentos medidos** en
  total (`casos × repeticiones`). Orden fijo: todas las preguntas en orden por
  repetición. Sin paralelismo, reintentos ni generación de preguntas. Una
  repetición es un intento nuevo, no un reintento automático de un fallo.
- `limit` de RAG entre 1 y 20. Calentamiento opcional de 0 a 2 llamadas de la
  primera pregunta; se registra aparte y no entra en las métricas medidas. Si
  falla cualquiera de esas llamadas, se detiene el lote antes de medir y se
  informa el motivo sin ocultarlo. Máximo de 32 llamadas contando calentamiento.
- Tiempo de espera de socket configurable, entero de 1 a 180 segundos, con
  180 por defecto. Es un límite para operaciones bloqueantes de conexión o
  socket, **no un plazo total garantizado** para toda la solicitud. Varias
  operaciones y la recepción gradual pueden prolongar el tiempo total.
  Python documenta el timeout de operaciones bloqueantes en
  [`http.client`](https://docs.python.org/3.12/library/http.client.html#http.client.HTTPConnection)
  y [`socket`](https://docs.python.org/3.12/library/socket.html#socket.socket.settimeout).
  Un timeout tampoco demuestra que el trabajo remoto se haya cancelado.
- La respuesta se lee con un máximo de 2.000.000 de bytes. No se imprimen ni
  guardan preguntas, respuestas, fragmentos, cabeceras de autorización, rutas
  de entrada o cuerpos de error. Los IDs de caso y de solicitud también pueden
  ser sensibles. El JSONL y los metadatos deben contener solo datos sintéticos
  y no deben incluir secretos.

La API local puede estar configurada para llamar a un proveedor externo: el
cliente **no puede comprobarlo** solo por conectarse a localhost. Para una
primera medición real se requiere verificar por separado que la API y sus
proveedores sean los previstos. El uso de OpenAI o de otros servicios externos
puede enviar datos o generar coste; requiere una decisión posterior. Este MVP
no mide tokens, coste, consumo de memoria, throughput ni capacidad bajo carga.

## Identificar la ejecución

Además del JSONL de casos, se propone un archivo de contexto JSON con estos
campos exactos: `schema_version` entero `1`, `embedding_provider`,
`embedding_model`, `generation_provider`, `generation_model`, `corpus_revision`,
`server_revision`, `hardware_note` y `min_score`. Los campos de texto son
cadenas imprimibles de 1 a 128 caracteres, sin saltos ni controles; se admite
`unknown` cuando no se conoce un valor. `min_score` es `null` si no se usa
umbral, `"unknown"` si se desconoce, o un número finito entre `-1` y `1` no
booleano. Los nombres de proveedor previstos son `ollama`, `simulated` o
`unknown` para esta etapa local. No se incluirán tokens, URLs con credenciales,
rutas privadas ni contenido de documentos en el contexto.

Ejemplo de contexto para un servidor simulado:

```json
{"schema_version":1,"embedding_provider":"simulated","embedding_model":"unknown","generation_provider":"simulated","generation_model":"unknown","corpus_revision":"synthetic-v1","server_revision":"fixture-v1","hardware_note":"unknown","min_score":null}
```

El archivo de contexto tendrá como máximo 65.536 bytes, sin BOM, con UTF-8
válido, claves exactas y sin duplicados. Se validarán los tipos y la finitud de
los números antes de conectar. Sus valores se copian al informe: el programa no
puede detectar de forma fiable si el operador introdujo un secreto o una ruta
privada en una cadena aparentemente válida.

El informe conservará los valores declarados, el SHA-256 de los bytes de
casos y contexto, y los parámetros `port`, `limit`, `socket_timeout_s`,
repeticiones y calentamiento. **Son datos declarados por el operador**, no una
verificación de la configuración real del servidor o del corpus. Un hash
identifica los bytes de un archivo, no garantiza su veracidad ni anonimiza
contenido predecible. Los límites y parámetros se validarán antes de iniciar
la primera conexión; entradas inválidas no deben producir solicitudes.

## Clasificación de intentos

Cada intento medido o de calentamiento tendrá fase, ID de caso, número de
repetición, duración observada en nanosegundos, código HTTP si se recibió y
un único resultado:

| Resultado | Significado |
| --- | --- |
| `ok_answered` | HTTP 200 y respuesta básica válida con `status: answered` y fuentes no vacías. |
| `ok_no_context` | HTTP 200 y respuesta básica válida con `status: no_context` y fuentes vacías. |
| `http_error` | Respuesta HTTP distinta de 200, recibida completamente dentro del límite. El código se registra; no se interpreta su cuerpo. |
| `timeout` | Expiró una operación bloqueante del cliente. El tiempo observado no es una latencia de respuesta completada. |
| `transport_error` | Falló la conexión o la comunicación, incluida una respuesta HTTP incompleta. |
| `framing_error` | Cabeceras o estructura HTTP incompatibles con el encuadre limitado de este cliente. |
| `invalid_response` | HTTP 200 completado, pero JSON o contrato básico inválidos. |
| `response_too_large` | El cuerpo supera el límite; se corta la lectura. |

Para poder distinguir un cuerpo completo de uno cortado, el MVP exigirá una
única cabecera `Content-Length` con entre 1 y 10 dígitos ASCII decimales y ninguna
`Transfer-Encoding`. Es una restricción del cliente, compatible con la API
RAG actual, que puede rechazar otras implementaciones HTTP válidas. Si la
longitud declarada excede 2.000.000 de bytes se registra
`response_too_large` sin leer el cuerpo. Si la conexión termina antes de la
longitud declarada se registra `transport_error`; hay que comprobar los bytes
leídos, no asumir que una lectura parcial lanzó una excepción. Cabeceras
ausentes, duplicadas o inválidas, transferencia codificada o una línea de
estado HTTP inválida producen `framing_error`. No se reutiliza la conexión.

Para un HTTP 200, la validación básica exige JSON UTF-8 sin BOM ni claves
duplicadas, números no finitos ni representaciones numéricas de más de 128
caracteres; objeto con `status`, `answer` no vacío y
`sources` como lista de hasta `limit` objetos con `document_id` válido. Se
comprueba la coherencia entre estado y lista vacía y se exige que las cadenas
utilizadas puedan codificarse en UTF-8. No se verifica aquí la
corrección de la respuesta, el texto completo de cada fuente ni la totalidad
del contrato de API. En cualquier otro HTTP se conserva solo el código, no el
cuerpo. Si tras recibir cabeceras se agota el timeout, se corta la conexión o
se excede el límite, prevalece el resultado de ese fallo y se conserva el
código HTTP si ya estaba disponible. Un HTTP `504` es `http_error`, no un
`timeout` del cliente.

El cliente guardará `request_id` únicamente si recibe una sola cabecera
`X-Request-ID` con exactamente 32 dígitos hexadecimales minúsculos, como los
generados por la API actual, y no coincide con el token de lectura; de lo
contrario será `null`. Puede servir para
buscar eventos del servidor, pero no se recopilan ni suman esas trazas aquí.
No se incluyen en el informe excepciones, cuerpos, valores de cabeceras no
validados ni texto de entrada.

### Detención y trabajo todavía activo

Durante los intentos medidos se detendrá el lote ante `timeout`,
`transport_error`, `framing_error`, `response_too_large` o HTTP `401`, `403`, `429`, `503` o
`504`. Se conserva el resultado que disparó la detención y se cuentan los
intentos planificados que no llegaron a iniciarse. Otros HTTP distintos de
200 y respuestas 200 inválidas se registran sin reintento y permiten continuar
con el siguiente caso. Esta política acota el envío tras fallos de conexión,
autorización, saturación o estado incierto; no demuestra que el servidor haya
dejado de trabajar. Aunque el cliente sea secuencial, un trabajo anterior
puede seguir activo después de una desconexión o timeout.

Si el error que dispara la política ocurre en el último intento, el informe
seguirá indicando `aborted_policy`, con cero intentos sin iniciar. Un fallo en
calentamiento produce `aborted_warmup`, sin intentos medidos. Una interrupción
del proceso no produce un informe completo garantizado; no se reinterpretarán
los casos no iniciados como respuestas fallidas.

## Métricas y denominadores

El informe diferenciará calentamiento y medición. Cada fase tendrá su propio
`planned`, `attempted`, `not_started` y resultados individuales; además se
indicará `run_status` y el motivo de detención cuando corresponda. Si falla
el calentamiento, se conservarán también los calentamientos no iniciados. Para
medición se cumple:

```text
attempted = ok_answered + ok_no_context + http_error + timeout
            + transport_error + framing_error + invalid_response + response_too_large
planned = attempted + not_started
```

`failure_rate_observed` será la suma de resultados no exitosos dividida entre
`attempted`, o `null` si no hubo intentos medidos. Es una tasa **condicional a
los intentos iniciados**, no a todos los planificados. Una ejecución detenida
antes de terminar no estima la tasa que se habría observado en los restantes.
Los fallos y el calentamiento no se mezclan con las distribuciones de latencia
de respuestas completas.

Se informarán recuento, mínimo, máximo, p50 y p95 de duraciones de respuestas
exitosas: en conjunto y separados para `ok_answered` y `ok_no_context`. Cada
grupo conserva su propio denominador; la distinción importa porque RAG puede
responder `no_context` sin llamar al modelo de generación. Se usará el método
de **rango más cercano**: ordenar las duraciones de cada grupo, calcular
`ceil(p × n)` con posiciones desde 1 y tomar el valor de esa posición. El p50
de cuatro valores es el segundo, no el promedio de los dos centrales.

Con cero muestras, mínimo, máximo y percentiles son `null`. Con menos de 20
muestras exitosas en un grupo, su p95 será `null` por la regla didáctica de
este MVP. Tener 20 muestras tampoco garantiza una estimación estable. No se
informará p99 ni se usará esta pequeña muestra para afirmar un SLO. Los
tiempos de fallos se conservarán por intento, pero no se incluirán en esos
percentiles; el tiempo de un timeout es solo lo observado hasta el fallo.

### Ejemplo sintético de cálculo

Supongamos siete intentos planificados, sin calentamiento, y estas seis
observaciones en orden. Los tiempos son inventados para ilustrar el cálculo:

| Intento | Resultado | Duración observada |
| --- | --- | ---: |
| 1 | `ok_answered` | 50 ms |
| 2 | `ok_answered` | 120 ms |
| 3 | `ok_answered` | 180 ms |
| 4 | `ok_no_context` | 220 ms |
| 5 | `http_error` (400) | 60 ms |
| 6 | `timeout` | 6.100 ms |

La política detiene el lote tras el timeout: `attempted = 6`,
`not_started = 1` y `failure_rate_observed = 2/6`. Entre las cuatro respuestas
exitosas, p50 es `120 ms`; p95 es `null` porque hay menos de 20. El grupo
`ok_answered` tiene tres muestras y `ok_no_context` una. El fallo rápido y el
timeout no reducen artificialmente los percentiles de éxito; tampoco se
pretende conocer cuánto habría tardado el trabajo sin el timeout.

## Informe y operación

Se propone un módulo Python independiente para ejecutar el lote y emitir un
único JSON a stdout. Guardará los intentos individuales y resúmenes, sin
contenido de conversación. Para un conjunto de eventos ya registrado, la
serialización será estable: claves ordenadas, escapes ASCII y LF final. **Dos
ejecuciones de red no serán deterministas**, aunque tengan las mismas entradas.
El contexto incluirá los valores declarados y sus hashes; no se añaden claves
ni archivos de credenciales a la imagen o al repositorio.

La interfaz implementada tiene esta forma:

```text
python -m evals_lab.live_metrics --cases CASOS.jsonl --context CONTEXTO.json --port 8080 --limit 2 --repeats 1 --warmup 0 --timeout-s 180
```

El token se proporciona por entorno, no como argumento. El puerto del ejemplo
es ilustrativo y no inicia el servidor. El cliente también rechaza antes de
conectar un ID de caso, campo del contexto o hash de entrada que contenga el token activo;
esto no detecta otros secretos o rutas que el operador pudiera incluir.

`0` indicará que se completaron todos los intentos medidos con respuestas
básicamente válidas. `1` indicará al menos un fallo medido o una detención de
calentamiento/política; se emitirá un informe con los denominadores reales.
`2` indicará configuración o entrada inválida, o que no se pudo producir el
informe; no se iniciará ninguna solicitud ante un error de validación previo.
`--help` terminará con `0`. Si stdout falla durante la escritura, puede quedar
una salida parcial que debe descartarse. Una interrupción forzada puede impedir
publicar el informe y no debe interpretarse como ejecución completa.

Los tiempos se obtienen en llamadas separadas de las evaluaciones offline y
este cliente descarta las respuestas. Por ello **no se puede atribuir una
puntuación de sustento o completitud al mismo texto de un intento**. Una
integración futura que conserve respuestas requeriría un contrato explícito,
tratamiento de datos y una decisión separada.

## Verificación y siguiente etapa

La implementación usa la biblioteca estándar y las pruebas usan un servidor
simulado de loopback, sin Ollama, PostgreSQL, OpenAI ni credenciales reales.
Se comprueban validación previa sin conexiones, orden y límites,
calentamiento fallido, éxitos de ambos estados, errores HTTP y de transporte,
timeout, encuadres inválidos, longitud leída y límite de cuerpo. También la
detención y los casos no iniciados, los denominadores nulos, el p50 de muestra par, p95 justo
por debajo y por encima del umbral, el uso del reloj y la exclusión de fallos
de percentiles. Las pruebas verifican que el token activo, cuerpos y rutas no se
filtre en informe o errores, y la paridad con Docker y los modos existentes.

Las mediciones con API y modelos reales requieren aprobación y una configuración
conocida; no se ejecutan como parte de CI.
