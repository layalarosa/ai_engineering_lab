# Diseño: plazo total y cancelación en `ask_rag`

**Estado: plazo total local implementado y probado.** Este documento conserva
el diseño y sus límites. Cubre una llamada MCP a RAG local; no añade
concurrencia a las sesiones, cancelación remota en RAG ni garantías de tiempo
real.

## Qué problema resolvemos

El timeout de socket de 270 segundos se aplica a operaciones bloqueantes; por
sí solo permite que una respuesta que entrega datos periódicamente tarde más
de 270 segundos. Como la sesión MCP procesa mensajes secuencialmente, durante
esa espera tampoco procesa nuevas llamadas o notificaciones. Los límites de
tamaño de respuesta no son un límite de tiempo.

Queremos limitar **la espera local de una ejecución de `ask_rag`**, incluso
si RAG envía cabeceras o cuerpo lentamente. El plazo no demuestra que el
trabajo remoto haya terminado. La [especificación MCP de ciclo de vida](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
recomienda que los clientes establezcan tiempos de espera; su mecanismo de
[cancelación](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/cancellation)
es opcional y distingue entre solicitar la cancelación y que pueda detenerse.

## Contrato implementado

- `MCP_RAG_TOTAL_TIMEOUT_SECONDS` se configura en el entorno del servidor,
  no en los argumentos que proporciona el modelo. Predeterminado: **270
  segundos**; entero entre **1 y 600**, escrito con hasta tres dígitos ASCII.
  Un valor ausente usa el predeterminado;
  uno inválido produce un error de herramienta sin consultar RAG. El mínimo y
  el máximo son políticas iniciales de laboratorio, no valores universales.
- El plazo usa un reloj monotónico y comienza al despachar `ask_rag`, una vez
  leída y analizada la línea MCP. Incluye validación, lectura de credenciales,
  creación del proceso auxiliar, comunicación interna y la petición HTTP:
  conexión, envío, cabeceras, cuerpo y validación del resultado.
- Si se agota, se detiene el proceso auxiliar, se intenta recogerlo y se
  devuelve un resultado de herramienta con `isError: true`, sin reintentar y
  sin datos de la pregunta o credencial. El mensaje indicará que la espera
  local terminó y que el procesamiento remoto podría continuar.
- El plazo es una **meta de control de la operación**, no una garantía dura
  de respuesta en tiempo real. La creación de procesos puede no ser
  interrumpible en algunas plataformas; la planificación del sistema, la
  terminación y la limpieza añaden demora. Leer la línea MCP antes del despacho
  y escribir o vaciar stdout hacia un cliente que no lee no quedan cubiertos.
  Python documenta estas limitaciones de [creación y espera de procesos](https://docs.python.org/3.10/library/subprocess.html#subprocess.run).
- El timeout de socket por inactividad se conserva como defensa adicional y
  no sustituye el plazo total. Una respuesta grande o incompleta, un fallo de
  autenticación o un resultado inválido conservan sus errores actuales. El
  presupuesto de 100 llamadas por sesión incluye las llamadas fallidas.
- El cliente MCP debe configurar su propio timeout con margen sobre el plazo
  de la herramienta para permitir inicio, limpieza y transmisión. Un valor de
  300 segundos puede ser razonable con el valor predeterminado, pero no es una
  garantía y debe ajustarse si cambia la configuración o el entorno.

## Proceso auxiliar supervisado

```text
cliente MCP → servidor MCP → proceso auxiliar → HTTP → API RAG → modelo
                  │               │
                  └── reloj y ────┘
                      supervisión
```

1. El servidor recibe la llamada, registra el instante monotónico de inicio y
   valida el plazo. Solo se permite **un proceso auxiliar activo por sesión**,
   coherente con el procesamiento secuencial actual.
2. Inicia un proceso Python interno sin shell y le entrega la pregunta y la
   configuración necesaria mediante un canal privado acotado. El auxiliar lee
   el archivo de credenciales, si corresponde, dentro del plazo supervisado;
   esto evita que un archivo o montaje bloqueante deje al padre esperando antes
   de iniciar el control. Las credenciales no se pasan en argumentos, no se
   escriben en archivos temporales ni se registran. El entorno del hijo debe
   minimizarse y excluir las variables de secretos que no necesita.
3. El auxiliar reutiliza las reglas HTTP existentes: solo `127.0.0.1`, sin
   proxies ni redirecciones, con los límites y validaciones de respuesta
   actuales. Devuelve al padre un resultado estructurado por un canal privado;
   su stdout no es el stdout MCP del servidor.
4. El padre supervisa **entrada y salida**, tamaño y duración. Debe limitar el
   mensaje de salida antes de acumularlo en memoria, validar su estructura y
   mantener el límite final de 256 KiB del mensaje MCP. Ante error interno,
   salida inválida o demasiado grande, devuelve un error genérico y no imprime
   datos internos. Antes de aceptar una respuesta completa, vuelve a comprobar
   el reloj: si el plazo ya venció, el timeout prevalece. La decisión se toma
   una sola vez para evitar dos respuestas en la carrera de finalización.
5. Al agotarse el plazo, el padre finaliza el auxiliar, cierra sus canales y
   espera su salida con una limpieza acotada de hasta dos segundos. Si no puede confirmar la
   limpieza, se cierra la sesión MCP con un diagnóstico genérico en stderr en
   vez de iniciar más auxiliares silenciosamente. En fallos del sistema no se
   puede prometer que un proceso quede recogido de inmediato.

La implementación debe funcionar tanto desde `python -m mcp_lab.server` como
desde el zipapp y la imagen Docker. El modo auxiliar sería un detalle interno
del mismo programa; se verificará que el proceso empaquetado encuentre el
código correcto sin depender del directorio de trabajo ni de un `PYTHONPATH`
externo. La comunicación y los errores internos no forman parte del contrato
público MCP.

La supervisión no debe limitarse a `Popen.communicate(input=..., timeout=...)`
como única defensa: en el Python 3.10 de Windows revisado, la escritura de
stdin ocurre antes de la espera con timeout y podría bloquear si el hijo no
lee. Además, `communicate` acumula la salida en memoria. Se requiere un diseño
con escritura y lectura supervisadas, límites de bytes y cierre de canales al
terminar el auxiliar. Se probará en Windows y Linux; los detalles internos de
Python pueden variar por versión.

Si el servidor MCP desaparece abruptamente, la limpieza del hijo no está
garantizada por el simple hecho de usar un subproceso. El auxiliar tiene una
**autolimitación de mejor esfuerzo** y comprueba el cierre por EOF del canal
privado; esto reduce el riesgo de procesos huérfanos, pero no equivale a una
garantía del sistema operativo ante fallos extremos. La supervisión del host
sigue siendo necesaria para un servicio compartido.

## Por qué no cambiar solo el timeout del socket

Cambiar el valor de `HTTPConnection(timeout=...)` no resuelve respuestas que
entregan bytes antes de cada timeout. Un hilo con un timeout en el padre
permitiría dejar de esperar, pero no garantiza detener el hilo o liberar su
socket; repetir llamadas podría acumular trabajo. Un proceso auxiliar tiene un
coste de arranque y coordinación, pero ofrece una unidad local que el padre
puede terminar y recoger en condiciones normales, sin añadir una dependencia
HTTP ni reimplementar el parser HTTP. El plazo y la limpieza deben verificarse
en cada plataforma; no se presume que el aislamiento resuelva el trabajo en
RAG.

## Cancelación: alcance y comportamiento

Esta primera mejora **no implementa** `notifications/cancelled` durante una
llamada. La lectura stdio sigue bloqueada hasta que termine la consulta o su
plazo; una notificación recibida mientras tanto no se procesa en tiempo real.
Un cliente puede dejar de esperar antes y debe tratar una respuesta posterior
como tardía. El deadline del servidor reduce el tiempo de espera local, pero
no sustituye la cancelación iniciada por el cliente.

Para admitir cancelación interactiva habría que permitir que el lector de
mensajes siga activo durante la consulta, asociar cada ejecución a su ID y
resolver carreras entre finalización, timeout, cierre de sesión y cancelación.
Eso es una etapa separada, que también requiere reevaluar el SDK oficial. Según
la especificación, el receptor debería dejar de procesar y no enviar una
respuesta a una petición cancelada cuando puede cumplirlo; no se debe anunciar
esa capacidad hasta implementar y probar ese comportamiento.

El `POST /v1/answers` de RAG no ofrece un contrato de cancelación de trabajo
en curso. El proceso de RAG puede continuar con la base, embeddings o modelo
después de que MCP cierre la conexión. No se promete liberar esos recursos ni
evitar costes remotos. No se reintenta automáticamente una petición cuya
finalización sea incierta.

## Pruebas y límites

1. Éxito, errores HTTP, configuración inválida y límites existentes; confirmar
   que las preguntas Unicode y la respuesta con fuentes no cambian.
2. Un servidor local que no responde, otro que envía cabeceras lentamente y
   otro que envía el cuerpo byte a byte con pausas menores que el timeout de
   socket. Debe vencer el plazo total con margen de planificación razonable.
3. Auxiliar que no lee stdin, que envía una salida mayor del límite o que
   termina de forma inesperada. Verificar cierre y recuperación sin publicar
   credenciales ni cuerpos en stdout/stderr.
4. Tras el vencimiento, comprobar el resultado `isError`, la ausencia de
   reintentos y que una llamada posterior a `text_stats` en la misma sesión
   funciona una vez completada la limpieza. Verificar la carrera cuando una
   respuesta llega cerca del plazo y que solo se emite un resultado.
5. En Windows y Linux, inspeccionar que no queden auxiliares tras éxito,
   timeout y error; probar también el cierre abrupto del padre y documentar
   sus límites. Probar desde código fuente, zipapp, imagen Docker y cliente
   MCP oficial. Los tests usarán plazos cortos inyectados internamente, no
   esperas de varios minutos.
6. Simular un backend que continúa procesando tras cerrarse la conexión y
   demostrar que el resultado local no implica cancelación remota. Conservar
   una prueba específica del caso Unicode corregido anteriormente.

La suite cubre estos casos con servidores locales y plazos cortos. Se ejecutó
en Windows con Python 3.10 y en un contenedor Linux con Python 3.12; la prueba
de integración en Docker ejercitó MCP, RAG y PostgreSQL con un modelo
simulado. La cancelación MCP concurrente y la propagación de cancelación a RAG
siguen siendo decisiones posteriores.
