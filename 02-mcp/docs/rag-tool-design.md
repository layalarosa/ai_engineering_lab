# Diseño: consulta RAG desde MCP

Estado: **primera integración implementada**. Esta etapa conecta dos
proyectos independientes mediante la API HTTP existente, sin importar código de
`01-rag` ni compartir acceso directo a su base de datos.

## Problema y recorrido

Un cliente MCP necesita consultar los documentos indexados en RAG sin conocer
los detalles de embeddings, búsqueda o generación. La herramienta propuesta
recibe una pregunta, llama a `POST /v1/answers` y devuelve la respuesta y sus
fuentes. El cliente decide si presenta la herramienta al usuario y si autoriza
su ejecución.

```text
Usuario → cliente MCP → ask_rag → API HTTP de RAG → búsqueda + modelo
                  ← resultado y fuentes ←
```

La operación usa una credencial de lectura de RAG, pero también invoca un
modelo: puede tardar, generar costes y enviar datos al proveedor configurado
en RAG. Las fuentes y el texto generado son datos no confiables; no deben
interpretarse como instrucciones para el cliente o el modelo.

## Contrato de la herramienta

Nombre: `ask_rag`. Está anunciada en `tools/list` junto a `text_stats`, aun
cuando RAG no esté configurado. Así `text_stats` sigue funcionando de forma
independiente y una configuración ausente produce un error de herramienta,
sin impedir que arranque el servidor.

Entrada, expresada como esquema JSON:

```json
{
  "type": "object",
  "properties": {
    "question": {"type": "string", "minLength": 1, "maxLength": 4000},
    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 3}
  },
  "required": ["question"],
  "additionalProperties": false
}
```

Además, se rechazarán preguntas compuestas solo de espacios y cadenas Unicode
inválidas. `limit` no acepta booleanos. El límite de 32 768 bytes del mensaje
MCP completo sigue aplicándose: según la codificación JSON elegida por el
cliente, una pregunta válida de 4000 caracteres puede superar ese límite. El
servidor aplicará estas validaciones aunque el cliente también las haga.

Ejemplo de llamada:

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"ask_rag","arguments":{"question":"¿Qué contiene el documento?","limit":3}}}
```

Una ejecución satisfactoria devuelve un resultado MCP con un elemento de
contenido `text`. Su texto es un objeto JSON serializado con los campos de la
API RAG: `status`, `answer` y `sources`. Cada fuente contiene `label`,
`document_id`, `chunk_index`, `score` y `text`. Se conservan las fuentes para
que el usuario pueda revisar el contexto empleado.

```json
{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"{\"status\":\"no_context\",\"answer\":\"No se encontró contexto relevante.\",\"sources\":[]}"}],"isError":false}}
```

`no_context` significa que la búsqueda no aportó fragmentos aptos. `answered`
indica que se generó una respuesta con contexto, no que sea correcta ni que
todas sus afirmaciones estén respaldadas. El modelo también puede abstenerse
cuando el estado es `answered`. Los ejemplos ilustran el contrato y no
certifican una respuesta específica del modelo.

## Configuración y confianza

La integración será opcional y requerirá configuración explícita del proceso
MCP:

- `MCP_RAG_BASE_URL`: origen HTTP local, por ejemplo
  `http://127.0.0.1:8000`. Solo se aceptará la dirección literal `127.0.0.1`,
  con puerto válido; se rechazarán credenciales en la URL, rutas distintas de
  `/`, consultas y fragmentos. Se añadirá una ruta fija `/v1/answers`.
- `MCP_RAG_READ_TOKEN` o `MCP_RAG_READ_TOKEN_FILE`: una sola de las dos
  opciones, con el mismo token de lectura configurado en RAG. El archivo será
  UTF-8 y tendrá un tamaño acotado; se eliminará únicamente el salto de línea
  final. No se aceptarán ambas opciones simultáneamente.

Las credenciales se leerán al ejecutar la herramienta para permitir su
rotación; nunca serán argumentos de la herramienta ni aparecerán en sus
respuestas o registros. La URL tampoco puede elegirse desde una llamada MCP.
El cliente HTTP ignorará los proxies definidos por el entorno y rechazará
redirecciones, evitando enviar el token a un destino diferente. Este diseño
confía en la máquina local: no protege frente a un proceso local comprometido
que suplante la API. No habilita el acceso a RAG remoto o a otros contenedores;
eso requiere diseñar por separado el transporte y la confianza.

El transporte stdio hereda los permisos del proceso que lo inicia. El host MCP
es responsable de proteger la configuración y controlar el uso de la
herramienta; esta integración no añade autenticación al propio stdio.

## Límites, fallos y observabilidad

- Cada intento consume una llamada del presupuesto existente de 100 llamadas
  por sesión, incluso si falta configuración o falla la API. Ese presupuesto
  no es un límite global entre procesos.
- El socket tiene un timeout de inactividad de 270 segundos. Además, el
  servidor supervisa un **plazo total local** de 270 segundos por defecto,
  configurable entre 1 y 600 mediante `MCP_RAG_TOTAL_TIMEOUT_SECONDS`. El
  timeout de socket puede vencer antes que un plazo total mayor. No es una
  garantía de tiempo real ni detiene necesariamente el trabajo de RAG; el
  cliente MCP debe fijar su propio tiempo de espera con margen. Consulta el
  [diseño del plazo](deadline-design.md).
- El servidor MCP actual procesa mensajes de forma secuencial: mientras
  espera la API no atiende otras solicitudes ni cancelaciones. Un timeout o el
  cierre del cliente no garantiza detener el trabajo ya iniciado en RAG.
- Se limitará la respuesta HTTP a 256 KiB antes de analizar el JSON y se
  comprobará también el tamaño de la salida MCP serializada. Una respuesta
  mayor se rechazará con un error explícito, sin truncar silenciosamente; por
  tanto, algunas respuestas válidas de RAG podrían exceder este límite.
- Se validarán codificación UTF-8, tipo de contenido JSON, estructura y tipos
  del resultado; las puntuaciones deben ser números finitos y los índices no
  negativos. Los datos incorrectos o incompletos se tratarán como fallos de la
  herramienta, nunca como una respuesta fiable.
- No habrá reintentos automáticos: repetir una generación puede duplicar el
  trabajo o el coste. El usuario podrá decidir si reintenta.
- Los fallos de configuración, validación de argumentos y ejecución se
  devolverán como resultado MCP con `isError: true` y un mensaje breve y
  seguro. Se distinguirán autenticación/autorización, servicio ocupado,
  indisponibilidad o timeout, y respuesta inválida o demasiado grande. No se
  devolverá el cuerpo de error arbitrario de RAG. Los errores propiamente del
  protocolo JSON-RPC conservarán su tratamiento actual.
- Los registros operativos, si son necesarios, irán a stderr, nunca a stdout;
  indicarán categoría y duración sin registrar token, pregunta, respuesta,
  fuentes ni cuerpos HTTP. No se prometerá una trazabilidad distribuida que
  todavía no existe.

## Organización propuesta

Se mantendrá por ahora el servidor MCP manual para conservar el objetivo de
aprendizaje y limitar el cambio. La interoperabilidad con el SDK oficial ya se
comprueba por separado; añadir una llamada HTTP no elimina la necesidad de
entender y mantener el ciclo de vida, la compatibilidad de versiones y los
límites del protocolo. Se reevaluará adoptar el SDK como dependencia de
ejecución cuando la ampliación del protocolo o la concurrencia justifiquen su
coste. La implementación manual actual no pretende afirmar conformidad MCP
integral ni soporta cancelación concurrente.

```text
02-mcp/src/mcp_lab/
  protocol.py      # registro y despacho MCP existentes
  tools.py         # herramienta local existente
  rag_client.py    # solicitud HTTP y validación del resultado
  config.py        # configuración opcional y credencial
```

La estructura es orientativa: se crearán archivos solo si la implementación
real los necesita. No se agregará un framework de agentes ni una dependencia
HTTP sin un beneficio concreto.

## Verificación prevista

1. Probar argumentos válidos e inválidos, incluidos Unicode, booleanos y
   propiedades adicionales, además del límite de tamaño del mensaje MCP.
2. Usar un servidor HTTP local de prueba para verificar ruta, autorización,
   estados `answered` y `no_context`, y que no se repitan las llamadas.
3. Probar credenciales ausentes o ambiguas, URL no permitidas, redirecciones,
   respuestas inválidas o sobredimensionadas, errores HTTP y timeout con
   esperas cortas controladas por las pruebas.
4. Comprobar que errores y registros no revelan credenciales ni contenido
   sensible, que `text_stats` sigue funcionando sin RAG y que el límite por
   sesión incluye los intentos fallidos.
5. Ejercitar la llamada de extremo a extremo mediante stdio y el cliente MCP
   oficial ya utilizado en las pruebas de interoperabilidad. Una prueba con
   RAG real complementará las anteriores cuando sus servicios estén activos.

Referencias: [diseño de la API RAG](../../01-rag/docs/api-design.md),
[herramientas MCP](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
y [transporte MCP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
