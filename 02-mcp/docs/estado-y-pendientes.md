# MCP: estado y pendientes de la revisión

Revisión del 15 de noviembre de 2026, actualizada tras corregir el problema
Unicode e implementar el plazo total local. Esta es una implementación
didáctica; no pretende ser un servidor MCP completo ni está preparada para
exposición pública.

## Qué está implementado y comprobado

- Un servidor MCP stdio que implementa un subconjunto de `2025-11-25`, con
  descubrimiento y ejecución de `text_stats` y `ask_rag`, límites por sesión,
  validación de entrada y errores diferenciados de protocolo y herramienta.
- Una integración HTTP local con RAG que usa token de lectura, limita el
  tamaño de las respuestas, no sigue redirecciones ni reintenta llamadas, y
  solo admite el destino `127.0.0.1` configurado por el operador.
- Un paquete zipapp y una imagen Docker sin dependencias adicionales para
  ejecutar MCP. El SDK oficial `mcp==2.3.0` se usa como cliente de prueba.
- Pruebas locales con servicio HTTP simulado; un laboratorio de contenedores
  con PostgreSQL y modelo simulado; y una prueba puntual con pgvector y Ollama
  reales. La prueba real solo comprueba un recorrido y una fuente sintética:
  no mide precisión general ni verifica automáticamente las afirmaciones.
- Tras implementar el plazo se ejecutaron 32 pruebas estándar en Windows con
  Python 3.10 y en un contenedor Linux con Python 3.12, además de 5 pruebas de
  interoperabilidad y el laboratorio Docker con la API RAG real y un modelo
  simulado. La prueba opcional con Ollama real quedó omitida en esa ejecución;
  se había ejecutado en una etapa anterior.

## Hallazgo 1: preguntas Unicode válidas pueden fallar en RAG

**Estado: corregido y probado.** La herramienta acepta hasta 4.000 puntos de
código. Anteriormente, [el cliente RAG](../src/mcp_lab/rag_client.py)
serializaba la pregunta con `ensure_ascii=True`. Algunos caracteres ocupaban
más bytes al convertirse en secuencias de escape JSON. La [API RAG](../../01-rag/src/rag/api.py)
limita el cuerpo JSON a 32.000 bytes antes de analizar la pregunta.

Se reprodujo con 4.000 caracteres U+1F600 y una petición MCP codificada como
UTF-8: el mensaje MCP medía 16.114 bytes, dentro de su límite de 32.768 bytes,
pero el cuerpo enviado a RAG medía 48.028 bytes. RAG respondió HTTP `413` y
MCP devolvió un error de herramienta genérico.

El cuerpo HTTP ahora se codifica como JSON UTF-8, preservando los escapes
obligatorios de JSON para los caracteres de control. Con `limit: 20`, los
4.000 emojis ocupan 16.029 bytes y 4.000 caracteres NUL ocupan 24.029 bytes;
ambos caben en el límite HTTP de RAG. Las pruebas comprueban el tamaño y la
reconstrucción exacta del cuerpo. Una prueba de extremo a extremo usa el SDK
MCP, la API RAG real, PostgreSQL y un proveedor de embeddings simulado, que
verifica mediante un hash que recibe cada pregunta íntegra.

El límite independiente de 32.768 bytes del mensaje MCP sigue vigente: un
cliente que escape todos los caracteres Unicode puede enviar una línea mayor
que ese límite aunque la pregunta tenga 4.000 caracteres. El servidor
rechazará esa línea según su contrato de transporte.

## Hallazgo 2: plazo total local

**Estado: plazo local implementado; cancelación interactiva pendiente.** El
valor de 270 segundos del socket sigue siendo un límite de inactividad, no una
fecha límite para el intercambio completo. Antes de la corrección, con un servidor temporal
que enviaba bytes lentamente se configuró un timeout de 0,1 segundos y la
llamada terminó correctamente después de 1,66 segundos: cada lectura recibió
datos antes de agotar su tiempo de inactividad.

Ahora un proceso auxiliar supervisado limita la duración local por defecto a
270 segundos; puede configurarse entre 1 y 600. Las pruebas cubren cabeceras y
cuerpos lentos, procesos que no leen, salida inválida o excesiva y recuperación
de la sesión. El servidor MCP sigue procesando mensajes secuencialmente;
mientras espera, no atiende otras peticiones ni una cancelación. Cerrar el
cliente o el proceso tampoco garantiza detener
una generación que ya comenzó en RAG. La especificación MCP recomienda
establecer tiempos de espera y un máximo de duración en el cliente; la
[cancelación](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/cancellation)
es opcional y no implica que un servicio externo se haya detenido.

La creación del proceso, la limpieza y el transporte stdio no ofrecen una
garantía estricta de tiempo real. La cancelación iniciada por el cliente y la
cancelación del trabajo remoto siguen pendientes. Adoptar un SDK, por sí solo,
no demuestra que se haya resuelto la cancelación del trabajo externo. El
[diseño](deadline-design.md) documenta estos límites.

## Cobertura y compatibilidad pendientes

- El [flujo MCP CI](../../.github/workflows/mcp-ci.yml) está configurado para
  ejecutarse ante cambios en cualquiera de los dos proyectos e incluye el
  laboratorio de contrato con la API RAG real, PostgreSQL y un modelo
  simulado. La prueba positiva se ejecutó localmente; también se comprobó que
  rechaza una respuesta incompatible introducida en una copia temporal de
  RAG. **Aún no se ha observado una ejecución en GitHub Actions.** Git muestra
  `02-mcp/` y el flujo como no rastreados; deben incorporarse al repositorio
  remoto antes de que GitHub pueda ejecutarlos. La prueba con Ollama real sigue
  siendo manual.
- Se probaron un cliente, transporte y operaciones concretos; no se ha
  certificado conformidad MCP integral ni soporte de la revisión `2026-07-28`,
  Streamable HTTP o clientes adicionales. El SDK oficial es una opción para
  reevaluar antes de ampliar el protocolo o introducir concurrencia.
- Los límites de peticiones y herramientas son por proceso, no un límite de
  tasa global. No hay autenticación del transporte stdio, autorización por
  usuario o documento, observabilidad distribuida ni control de acceso de
  producción. El host debe controlar el inicio y uso del proceso MCP.
- Las respuestas y fuentes pueden contener datos sensibles o instrucciones
  no confiables. Las pruebas de conectividad no garantizan que el modelo cite
  correctamente ni que esté protegido frente a instrucciones maliciosas.
- La integración Docker de red compartida es para laboratorio y requiere
  confiar en el host y el daemon. MCP comparte las rutas de red de RAG. En la
  variante con Ollama real, el intermediario temporal tiene salida de red y
  no proporciona cifrado ni una frontera frente a un proceso comprometido.

## Orden propuesto

1. Comprobar la ejecución real de GitHub Actions cuando los archivos estén en
   el repositorio remoto y decidir si hace falta ampliar MCP o continuar con el
   siguiente componente del laboratorio.
