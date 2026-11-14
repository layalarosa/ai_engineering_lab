# Propuesta: API interna para RAG

**Estado: primera versión local implementada.** Este documento conserva el diseño y los requisitos previstos. La implementación y sus límites actuales se describen en la sección de API del README. El control de tasa, TLS y la protección ante clientes hostiles siguen siendo responsabilidades externas; no es un servicio listo para exposición pública.

## Caso de integración

Una aplicación interna de preguntas y respuestas necesita indexar documentación de un equipo y hacer consultas desde otro proceso. Ambos procesos pertenecen a un mismo entorno de confianza y trabajan con **un único corpus compartido**. Un servicio autorizado administra documentos; otro puede consultar el corpus. Esta primera versión no cubre usuarios finales, permisos por documento, múltiples organizaciones ni acceso público directo.

La API sería un proceso Python pequeño junto a PostgreSQL y el proveedor de modelos. Reutilizaría la fragmentación, los clientes de embeddings, la búsqueda y la generación existentes. En vez de introducir una cola o un framework de agentes, la primera versión procesaría cada petición de forma síncrona. Es una opción apropiada para documentos pequeños y bajo volumen; tiempos de respuesta, límites y concurrencia deben medirse antes de adoptarla en otros escenarios.

## Alcance y configuración

- Prefijo de versión `/v1`. Entrada y salida JSON en UTF-8, salvo el cuerpo de documento indicado abajo.
- Un proveedor y modelo de embeddings configurados en el servidor por despliegue; el cliente no puede elegirlos por petición. Lo mismo se aplica al proveedor y modelo de generación. Cambiar el modelo de embeddings requiere un corpus reindexado antes de activar consultas; nunca comparar vectores de modelos distintos.
- No aceptar URLs, rutas del servidor, claves de proveedores ni nombres de modelo desde las peticiones. El servidor utiliza únicamente credenciales y destinos configurados por el operador.
- Los ejemplos siguientes describen la estructura, no garantizan que la generación cite correctamente o que la similitud implique relevancia.

## Contrato propuesto

### Indexar o sustituir un documento

`PUT /v1/documents/{document_id}`

Requiere permiso `documents:write`. El identificador lo asigna el cliente, tiene de 1 a 64 caracteres ASCII (`a-z`, `A-Z`, `0-9`, `_`, `-`) y no es una ruta. El cuerpo es el documento completo como `Content-Type: text/plain; charset=utf-8`, hasta 1.000.000 de bytes, no vacío ni compuesto únicamente de espacios. Los límites de fragmentación actuales (500 caracteres, solapamiento 50 y máximo 1.000 fragmentos) se mantienen inicialmente.

El servidor valida, fragmenta y obtiene los embeddings antes de sustituir atómicamente los fragmentos del identificador y modelo configurado. En caso de fallo previo al commit, se conserva la versión anterior. Una respuesta `200 OK` confirma que la transacción terminó:

```json
{"document_id":"guia-rag","chunks_indexed":3}
```

Repetir un `PUT` con el mismo contenido deja la misma versión lógica del documento, aunque puede recalcular embeddings y generar coste. Las escrituras simultáneas sobre el mismo documento deben serializarse por identificador y aplicar la última transacción completada; no se garantiza el orden de llegada. Si el cliente recibe un timeout, el resultado puede ser incierto y puede repetir la operación con el mismo contenido. No hay eliminación en esta primera propuesta. La ruta del archivo local nunca forma parte de la identidad expuesta.

### Consultar el corpus

`POST /v1/answers`

Requiere permiso `answers:read`. El cuerpo admite `question` (cadena no vacía, máximo 4.000 caracteres) y `limit` opcional (entero de 1 a 20, por defecto 3). El umbral de similitud, si se utiliza, se configura en el servidor y debe calibrarse con el corpus y el modelo.

```json
{"question":"¿Qué es un embedding?","limit":3}
```

Una respuesta `200 OK` con contexto tiene este formato:

```json
{
  "status": "answered",
  "answer": "Un embedding es una representación numérica de texto [1].",
  "sources": [{"label":"[1]","document_id":"guia-rag","chunk_index":0,"score":0.82,"text":"Un embedding es una representación numérica de texto."}]
}
```

Cuando no hay fragmentos que cumplan el umbral configurado, devuelve `200` con `status: "no_context"`, una explicación y `sources: []`, sin llamar al modelo de generación. `answered` significa que se obtuvo texto del modelo, no que su contenido esté verificado. Si los fragmentos existen pero son insuficientes, el modelo recibe instrucciones para reconocerlo; su respuesta no se valida automáticamente. Las fuentes son los fragmentos enviados al modelo, no citas verificadas. El contexto total conserva inicialmente el límite de 20.000 caracteres. La salida puede contener datos del corpus y debe tratarse como sensible.

### Errores y reintentos

Los errores de aplicación de las operaciones implementadas usan `Content-Type: application/json` y un cuerpo estable, sin trazas ni detalles internos. Los errores del protocolo HTTP rechazados por el servidor básico de Python, los métodos no admitidos y los errores del proxy pueden tener otro formato:

```json
{"error":{"code":"invalid_request","message":"La solicitud no es válida.","request_id":"identificador-opaco"}}
```

| HTTP | Código | Significado y tratamiento |
| --- | --- | --- |
| 400 | `invalid_request` | JSON, UTF-8, parámetros o identificador inválidos; corregir antes de reintentar. |
| 401 | `unauthenticated` | Credencial ausente o incorrecta; sin detalles sobre la causa. |
| 403 | `forbidden` | Credencial válida sin el permiso necesario. |
| 413 | `payload_too_large` | Cuerpo superior al límite de la operación. |
| 415 | `unsupported_media_type` | Tipo de contenido no admitido. |
| 422 | `unprocessable_document` | Documento vacío, solo espacios o que excede el límite de fragmentos. |
| 429 | `rate_limited` | Límite de solicitudes alcanzado; incluir `Retry-After` en segundos. |
| 502 | `upstream_error` | Respuesta inválida o fallo del proveedor; no devolver su cuerpo ni credenciales. |
| 503 | `service_unavailable` | Base de datos o proveedor temporalmente inaccesible, o capacidad agotada. Al rechazar una conexión saturada antes de leer su cuerpo, el sistema operativo puede cerrarla antes de que el cliente reciba el 503. |
| 504 | `upstream_timeout` | Tiempo de espera de una dependencia agotado; en indexación el resultado puede ser incierto. |
| 500 | `internal_error` | Fallo inesperado; investigar con el identificador de solicitud. |

Los reintentos deben ser limitados y con espera creciente. No reintentar automáticamente una generación ante un resultado incierto: podría duplicar coste. `PUT` admite repetición del mismo documento con el coste indicado arriba.

## Seguridad y operación requeridas antes de exponerla

1. **Autenticación y autorización.** Propuesta inicial: dos credenciales de servicio independientes, una con `documents:write` y otra con `answers:read`, entregadas mediante `Authorization: Bearer …`. El servidor verifica los valores con comparación resistente a variaciones de tiempo; se inician desde secretos del entorno, nunca desde el repositorio. Las credenciales deben tener alta entropía, poder rotarse y no aparecer en registros. La API falla al iniciar si faltan o son iguales. Esta solución sirve para un único corpus interno, no sustituye una identidad individual ni permisos por documento.
2. **Red y transporte.** Escuchar en loopback o una red privada y usar TLS en el proxy de confianza para conexiones entre máquinas. No publicar PostgreSQL ni Ollama directamente; el proxy no debe registrar cabeceras de autorización ni cuerpos. Para acceso remoto a proveedores, validar TLS y configurar explícitamente los destinos confiables.
3. **Aislamiento del corpus.** Cualquier cliente con `answers:read` puede recibir texto de cualquier documento indexado. No usar esta versión para documentos de distintas organizaciones o niveles de acceso. La tabla actual no implementa aislamiento por inquilino ni permisos por documento.
4. **Abuso y recursos.** Rechazar cuerpos superiores al límite antes de cargarlos por completo, aplicar límites de concurrencia y de tasa por credencial en el proxy, y configurar plazos explícitos. La aplicación limita a ocho peticiones activas; el control de tasa y la respuesta 429 con `Retry-After` dependen de un proxy aún no configurado. Las cuotas y plazos concretos deben fijarse mediante mediciones.
5. **Datos y generación.** Tratar documentos y preguntas como datos no confiables. Un prompt no elimina la inyección de instrucciones ni verifica afirmaciones. Cuando se configure OpenAI, los textos correspondientes saldrán del entorno local y pueden generar coste; revisar la política de datos aplicable antes de habilitarlo.
6. **Observabilidad.** Generar un `request_id` en el servidor y correlacionarlo con eventos de etapa. Registrar solo identificador, etapa, duración, estado y proveedor; no registrar cuerpos, preguntas, respuestas, rutas, claves ni cabeceras. Los errores públicos deben usar mensajes seguros, no excepciones de PostgreSQL ni del proveedor. El código CLI actual imprime algunos detalles de error y debe adaptarse antes de reutilizarse en HTTP.
7. **Persistencia.** Usar transacciones y consultas parametrizadas; la identidad de documento de la API no debe convertirse en una ruta de archivo. Una cuenta de aplicación debe tener solo los permisos de base requeridos; ejecutar la creación de la extensión y el esquema como paso de administración independiente.

## Trabajo para una exposición más robusta

- Sustituir o situar el servidor HTTP básico detrás de un servidor y proxy adecuados, con TLS, control de tasa, plazos y límites frente a clientes lentos.
- Afinar límites y permisos de base de datos para el entorno de despliegue y automatizar la configuración del proxy.
- Ampliar las pruebas de carga, rotación de credenciales y fallos reales de proveedores antes de utilizar la API fuera del laboratorio.
