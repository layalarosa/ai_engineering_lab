# Prueba sostenida breve y timeout del proxy

Ejecución local el 14 de noviembre de 2026 en Docker Desktop: tres contenedores cliente con IP distintas, dos hilos por cliente y una ventana de inicio de solicitudes de 12 segundos. Las peticiones ya iniciadas se dejaron finalizar. El proveedor era simulado, con 0,4 s de demora por llamada; se usó un documento sintético. El perfil aislado permite hasta dos solicitudes activas por IP y cuatro globales, y tiene una tasa de prueba elevada de 120 solicitudes/minuto por IP y ráfaga de 20. No es configuración de producción.

| Resultado | Observación |
| --- | ---: |
| Solicitudes iniciadas | 162 |
| HTTP 200, con la fuente esperada | 52 |
| HTTP 429 | 42 |
| HTTP 503 | 68 |
| Errores de conexión o respuestas 200 inválidas | 0 |
| Latencia p50 de los 52 éxitos | 823 ms |
| Latencia p95 de los 52 éxitos | 849 ms |
| Latencia p99 | No informada: menos de 100 éxitos |

Los percentiles usan el método de rango más próximo y excluyen los rechazos. La ventana es breve y una muestra de 52 éxitos no es suficiente para establecer una latencia de cola estable. Los clientes reintentan después de 0,2 s sin espera creciente; los rechazos rápidos aumentan la tasa ofrecida. Dos clientes completaron 22 peticiones cada uno y el tercero 8: en esta ejecución hubo reparto desigual, sin evidencia suficiente para caracterizar equidad en general. Se observaron 429 y 503 bajo el perfil de prueba, pero el 503 por sí solo no identifica de forma inequívoca qué capa rechazó cada solicitud.

`docker stats --no-stream` produjo cuatro muestras por servicio durante la ventana. Los máximos **observados en esas muestras**, no picos reales garantizados, fueron:

| Contenedor | CPU máxima muestreada | Memoria máxima muestreada |
| --- | ---: | ---: |
| Proxy | 1,64 % | 4,10 MiB |
| API | 5,03 % | 36,48 MiB |
| PostgreSQL | 8,43 % | 38,04 MiB |
| Modelo simulado | 1,03 % | 15,20 MiB |

La CPU de Docker se expresa como porcentaje de uso de CPU del contenedor; su interpretación depende del host y de la configuración de Docker. No se midieron los recursos de un modelo real, del host completo ni de la red.

En una prueba separada, el modelo simulado tardó 1,3 s por llamada y el proxy se configuró con `proxy_read_timeout` de 2 s. La indexación de una sola llamada terminó y una consulta con dos llamadas recibió HTTP 504; el cliente observó aproximadamente 4,05 s incluyendo conexión y transporte local. Una escritura posterior recibió 200. Este resultado comprueba el timeout del proxy en esa condición, pero **no demuestra que el trabajo del modelo se cancelara** ni define un plazo total de extremo a extremo.

Estas observaciones no son un SLO, una prueba prolongada de estabilidad ni una medición de capacidad con Ollama u OpenAI. Quedan pendientes tráfico más prolongado, muestras suficientes para colas, comportamiento ante clientes lentos, interrupción cerca del commit, límites bajo múltiples réplicas y mediciones en el entorno objetivo. Ejecutar con servicios simulados y datos sintéticos; no dirigir carga automáticamente a proveedores externos.
