# Diseño: MCP y RAG en contenedores

**Estado: laboratorio implementado y comprobado con modelo simulado y Ollama real.**
Se ha probado la API RAG real con PostgreSQL y MCP en contenedores de prueba.
La variante con Ollama real utiliza un intermediario temporal con salida de red;
esta etapa no habilita acceso entre máquinas, despliegue compartido ni uso en
producción.

## El problema

Hoy MCP solo acepta `http://127.0.0.1:<puerto>` y RAG escucha en
`127.0.0.1` dentro del proceso que ejecuta su API. Cuando cada proceso tiene
su propia red de contenedor, esas direcciones no señalan al mismo lugar: MCP
intenta conectarse a su propio contenedor. Publicar un puerto o usar el nombre
DNS del servicio tampoco cambia la interfaz en la que RAG está escuchando.

El cliente MCP se ejecuta en el equipo e inicia el servidor como proceso hijo.
Por ello el transporte **cliente ↔ MCP sigue siendo stdio**, mientras que
**MCP ↔ RAG sigue siendo HTTP**. La operación RAG conserva el mismo token de
lectura, contrato y límites; generar una respuesta puede consumir recursos y
enviar datos al proveedor configurado.

## Opciones consideradas

| Opción | Ventaja | Coste y límites |
| --- | --- | --- |
| **Compartir la red del contenedor RAG** | Ambos procesos ven el mismo `127.0.0.1`; no hay que abrir la API ni ampliar los destinos aceptados. | Comparten toda la pila de red: MCP podrá alcanzar los servicios y redes accesibles desde RAG. Deben ejecutarse en el mismo Docker Engine. |
| Contenedores separados en una red bridge | Cada contenedor tiene su propia red y puede descubrir al otro por nombre. | La API tendría que escuchar en una interfaz alcanzable y MCP tendría que aceptar y resolver un nombre. HTTP con bearer token no cifra ni autentica al servidor; habría que definir controles adicionales. |
| Acceder al equipo mediante `host.docker.internal` | Docker Desktop ofrece un nombre para llegar al host. | La API actual solo escucha en loopback, y MCP rechaza ese nombre. No basta con cambiar la URL; cambiar la interfaz o usar un intermediario amplía la superficie de acceso. |
| Red de host (`--network host`) | En entornos compatibles puede permitir acceso a servicios locales del host. | Comparte la red del host y depende de la plataforma y configuración. Docker Desktop exige habilitar expresamente esa función en versiones compatibles. |

Docker documenta la resolución de nombres en [redes de Compose](https://docs.docker.com/compose/how-tos/networking/), el [acceso al host en Docker Desktop](https://docs.docker.com/desktop/features/networking/networking-how-tos/) y los requisitos del [modo host](https://docs.docker.com/engine/network/drivers/host/).

## Decisión propuesta: red compartida con la API

En un único equipo de desarrollo con contenedores Linux, ejecutar la API RAG
en un contenedor de larga vida. El cliente MCP iniciará cada proceso MCP en
un contenedor efímero usando el modo Docker `--network container:<id-api>`.
Así ambos comparten el espacio de red y MCP conecta al `127.0.0.1` donde la
API ya escucha. Docker describe este modo en su [documentación de redes de
contenedores](https://docs.docker.com/engine/network/#container-networks).

```text
Equipo: cliente MCP
    │ stdin / stdout
    ▼
Contenedor MCP ── HTTP a 127.0.0.1:8080 ──► Contenedor API RAG
     └──────────── misma pila de red ─────────────┘
                                                  │
                            red de laboratorio ───┼──► PostgreSQL + pgvector
                                                  └──► Ollama accesible para RAG
```

El diagrama representa procesos con redes compartidas, no dos conexiones de
red independientes. La API seguirá enlazada solo a loopback y no se publicará
su puerto. No se modificará `MCP_RAG_BASE_URL` para aceptar direcciones de red
ni se añadirá un modo HTTP público a MCP. RAG ya tiene una imagen Python con
sus dependencias; habría que iniciarla con el módulo `rag.api` en lugar del
comando CLI predeterminado, e inicializar el esquema de la base por separado.

La red que use RAG para PostgreSQL y Ollama debe configurarse explícitamente.
Si Ollama sigue en el host, su dirección y su interfaz de escucha deben ser
alcanzables desde esa red; `host.docker.internal` por sí solo no demuestra
que un servicio enlazado solo a loopback sea accesible. Otra opción es un
Ollama en la red de laboratorio. En Docker Desktop para Windows se probó
un intermediario temporal conectado a la red interna y a otra red con salida,
sin exponer la API RAG. Su comportamiento y sus límites están descritos en el
[README](../README.md#prueba-en-contenedores-con-ollama-real). Las redes Docker
facilitan conectividad y aislamiento operativo, pero no cifran tráfico ni
autentican servicios.

## Límite de confianza y credenciales

- Compartir la red **no equivale a aislar MCP de RAG**: si MCP queda
  comprometido, puede intentar llegar a PostgreSQL, Ollama y cualquier otra
  dirección accesible desde el espacio de red compartido. También podría
  intentar usar la API de escritura, aunque no reciba el token de escritura.
  No se le entregarán la contraseña de la base ni el token de escritura.
- Se confía en el operador local, Docker Engine, el host y los contenedores
  participantes. Un operador con control del daemon puede inspeccionar o
  modificar contenedores y secretos. Este diseño no protege frente a un host,
  daemon o contenedor participante comprometido.
- La API y MCP reciben el **mismo token de lectura** mediante archivos
  montados de solo lectura, con `RAG_READ_TOKEN_FILE` y
  `MCP_RAG_READ_TOKEN_FILE`. El token de escritura se monta únicamente en
  RAG y en el flujo autorizado de indexación. Los archivos no se copian en
  imágenes, repositorio, mensajes MCP ni argumentos de herramientas. Hay que
  comprobar que los usuarios sin privilegios de ambos contenedores puedan leer
  los archivos y que otros usuarios no tengan acceso indebido: los permisos de
  montajes pueden variar entre Linux y Docker Desktop.
- El montaje de solo lectura evita escribir a través de ese montaje; no cifra
  el archivo ni lo oculta al host o al administrador Docker. Tampoco se asume
  que un secreto de Compose tenga las propiedades de un gestor remoto de
  secretos. No se colocará el valor del token en variables del contenedor para
  evitar su exposición directa en la configuración inspeccionable de Docker.
- La API lee sus credenciales al arrancar; MCP lee su archivo por llamada.
  Una rotación debe coordinar ambos lados y reiniciar o recrear los procesos
  para garantizar que apuntan a la misma versión, especialmente si se
  reemplaza el archivo montado. Durante la transición pueden ocurrir 401.
- HTTP en loopback no aporta cifrado ni identidad criptográfica. La decisión
  solo es aceptable bajo la confianza local descrita; no se traslada sin más a
  redes compartidas u otros hosts. Las fuentes siguen siendo datos sensibles y
  no confiables, y los errores y registros no deben contener tokens ni cuerpos.

## Ciclo de vida y fallos

1. Preparar un corpus y base de laboratorio, modelos disponibles, secretos y
   esquema de PostgreSQL. La indexación usa exclusivamente el permiso de
   escritura; la consulta usa exclusivamente el de lectura.
2. Arrancar la API en su contenedor y comprobar que escucha en loopback. Una
   conexión al puerto solo confirma que hay un proceso escuchando: no prueba
   que PostgreSQL y el modelo estén listos.
3. El cliente inicia MCP como proceso Docker efímero, sin terminal `-t`,
   compartiendo la red del **contenedor concreto** de la API. Stdio mantiene
   una sesión y su presupuesto de llamadas por proceso.
4. Si la API se detiene, MCP devuelve el error de conexión previsto. Al
   reiniciar o recrear la API, se volverá a identificar el contenedor y se
   iniciará una nueva sesión MCP; no se asumirá que una sesión existente se
   reconecta a la nueva instancia. No se reintenta automáticamente una
   generación que pudo haber empezado.
5. El cliente cierra stdin y espera el cierre del proceso; el contenedor MCP
   efímero se elimina al finalizar. Los datos de PostgreSQL y las imágenes
   persistentes del usuario no se eliminan como parte de una sesión.

La API no tiene aún una comprobación de salud integral ni una garantía de
cancelación. MCP supervisa un plazo total local además del timeout de
inactividad del socket; mientras espera no procesa mensajes adicionales y el
trabajo remoto puede continuar después del vencimiento.

## Verificación de laboratorio

1. Se construyó una instalación de prueba aislada con PostgreSQL y un modelo
   simulado, sin reutilizar datos de usuario. Se comprobó que la API está
   enlazada a loopback y no publica su puerto.
2. Se inició MCP con el SDK oficial mediante `docker run -i --rm` en la red del
   contenedor de la API. Se consultó una base vacía para comprobar
   `no_context`; después se indexó un documento sintético y se verificaron
   la respuesta y la fuente.
3. Se comprobaron una red independiente, una credencial incorrecta, el intento
   de escribir con el token de lectura, el límite de pregunta y los contadores
   del modelo. Se detuvo y recreó la API y se inició una sesión MCP nueva.
4. En Docker Desktop para Windows se comprobaron lectura y montaje de solo
   lectura de los secretos por los usuarios de las imágenes y una rotación
   coordinada mediante escritura en el mismo archivo y recreación de la API.
   No se han comprobado los permisos en un host Linux independiente ni todos
   los mecanismos de reemplazo de archivos o las ACL del host.
5. Se repitió el recorrido con Ollama real mediante el intermediario temporal
   descrito en el README. Se verificaron disponibilidad de modelos, ausencia de
   acceso directo de la API al host y bloqueo de rutas no permitidas. La prueba
   no mide calidad general ni protege frente a un intermediario comprometido.

La prueba se ejecuta mediante `interop/container_fixture/run.py`. Emplea un
proyecto Compose de nombre aleatorio, una red interna, una base en memoria y
tokens de prueba generados para la ejecución. La limpieza elimina los
contenedores y etiquetas propios en condiciones normales o ante fallos
controlados; una terminación forzada puede impedirla. La caché de compilación
de Docker puede permanecer. Las instrucciones y dependencias están en el
[README de MCP](../README.md#prueba-de-red-compartida-en-contenedores). El
flujo MCP CI incluye esta variante simulada con Python 3.12; aún queda por
observar una ejecución en GitHub Actions.

Para una red entre máquinas o con participantes no confiables se requiere un
diseño distinto: TLS con validación de identidad, distribución y rotación de
credenciales, autorización, control de destinos y salida de red, límites,
observabilidad y revisión de la API actual antes de exponerla. No basta con
permitir nombres de host arbitrarios o añadir `0.0.0.0` a la configuración.
