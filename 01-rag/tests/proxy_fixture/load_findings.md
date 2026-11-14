# Prueba exploratoria de carga y fallos

Ejecución local del 14 de noviembre de 2026 con Docker Desktop, Nginx, Uvicorn y PostgreSQL temporales. Se usó un proveedor **simulado** con 0,4 s de demora por llamada, un documento sintético y solicitudes HTTPS a localhost. El perfil de carga aumentó temporalmente el límite de tasa a 120 solicitudes/minuto con ráfaga de 20 para observar el límite de concurrencia; no es el perfil de configuración habitual ni una propuesta de producción. Se mantuvieron los límites de dos solicitudes activas por IP y cuatro globales.

| Peticiones lanzadas simultáneamente | HTTP 200 | HTTP 503 | Mediana observada de las respuestas 200 |
| ---: | ---: | ---: | ---: |
| 1 | 1 | 0 | 2,88 s |
| 2 | 2 | 0 | 2,86 s |
| 4 | 2 | 2 | 2,86 s |
| 6 | 2 | 4 | 2,86 s |

La limitación por IP explica los rechazos observados; esta prueba **no aisló ni midió el límite global de cuatro**, porque todos los clientes compartían una IP. Con una o dos respuestas exitosas por grupo no hay muestras suficientes para informar p95 o p99 con utilidad. Las duraciones incluyen cliente, TLS, proxy, API, base, modelo simulado y el entorno local. No son tiempos de un modelo real ni establecen rendimiento sostenido o capacidad para usuarios reales.

Al detener por separado el modelo simulado y PostgreSQL, la siguiente consulta recibió 503 en ambos casos. Tras reiniciar cada servicio, una consulta volvió a responder 200 con el documento de prueba como fuente. En esta ejecución, el tiempo desde iniciar cada comando de reinicio hasta observar el primer éxito fue de aproximadamente 3,2 s; incluye el comando Docker, la espera y la consulta, y no es un SLO de recuperación.

El guion comprueba estos comportamientos y limpia el entorno temporal. Es una prueba corta de ráfagas y fallos concretos, no una prueba de carga sostenida, saturación con múltiples IP, corte cerca de un commit, TLS entre hosts, clientes lentos, consumo de CPU o memoria, ni funcionamiento con un proveedor remoto. Para el diseño de una operación compartida siguen pendientes cargas de mayor duración, medición de recursos, presupuestos de tiempo y validación en el entorno objetivo.
