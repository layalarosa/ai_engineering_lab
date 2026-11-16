# Demostración de procedencia en proceso

Este experimento muestra cómo vincular una afirmación a un mensaje generado a
partir de sus datos y cómo corregir una versión anterior. **La identidad y la
política son simuladas:** cualquier código con acceso al objeto puede indicar
el nombre de un actor permitido. No hay autenticación, interfaz de confirmación,
aislamiento entre usuarios ni protección frente a un llamador malicioso.

La demostración no modifica la API de `memory_lab`, PostgreSQL ni el prototipo
de recuperación congelado. Su estado vive únicamente en el proceso y se pierde
al terminar. La llamada `confirm` **representa** una confirmación de la
aplicación; el código no demuestra que una persona haya visto o aprobado nada.

## Ejecutar

Desde `03-memory`, con Python 3.10 o superior, sin paquetes ni servicios:

```powershell
python -S experiments/provenance/demo.py
python -S -m unittest discover -s tests -p test_provenance_lab.py -v
```

El programa usa datos sintéticos y muestra el mensaje preparado para
confirmación, el mensaje guardado, el vínculo de corrección y el resultado de
repetir la misma operación. Las operaciones de la biblioteca experimental no
imprimen contenido por sí mismas; la demostración sí imprime su ejemplo.

## Cómo funciona

1. `ProvenanceLab` recibe un conjunto fijo de nombres permitidos por una
   política **simulada**. Esta política es la misma para todas sus sesiones;
   no distingue propietarios ni verifica credenciales.
2. `create_session` genera una generación nueva para un UUID de sesión elegido
   por el llamador. Solo existen dos claves de ejemplo: idioma y editor
   preferidos. `preview` valida clave y valor y devuelve el contenido exacto
   que `confirm` generará.
3. El contenido canónico es JSON con `clave` y `valor`, sin normalizar el valor.
   Los caracteres Unicode y NUL se representan mediante escapes JSON y se
   recuperan al decodificarlo. Se valida el **texto serializado** con el
   contrato de mensajes de `ConversationMemory`: un valor que cabe por sí solo
   puede exceder los 4.000 caracteres después de escapar y se rechaza.
4. `confirm` guarda en un solo cambio de estado en memoria el mensaje, su ID,
   la afirmación, el actor simulado y el ID de operación. Una corrección debe
   referenciar la afirmación vigente de la misma clave. El historial anterior
   permanece disponible hasta que se vacíe o elimine la sesión.
5. Un reintento autorizado con la misma sesión, generación, actor, ID de
   operación y solicitud estable devuelve la misma afirmación. Reutilizar el
   ID con un valor o predecesor distinto produce conflicto. La comprobación
   del reintento precede a la de la versión vigente.
6. `clear` borra el estado activo y cambia la generación; `delete` elimina la
   sesión. Recrear el mismo UUID genera otra generación. Las solicitudes con
   una generación anterior se rechazan; no hay deduplicación entre generaciones
   ni después de terminar el proceso.

El programa conserva como máximo 1.000 afirmaciones por sesión, igual que el
límite de mensajes del módulo actual. Las claves se comparan exactamente y los
UUID identifican datos: no autorizan acceso. Si la política simulada admite a
un emisor equivocado, el programa también atribuirá su afirmación. Del mismo
modo, una persona autorizada podría confirmar un valor falso: la procedencia no
lo convierte en verdad.

## Concurrencia y límites de la simulación

Una instancia usa un bloqueo de Python para serializar sus operaciones entre
hilos; ese bloqueo cubre todas sus sesiones. Cada escritura construye una
instantánea nueva antes de reemplazar el estado. Las pruebas coordinan dos
correcciones y dos reintentos simultáneos **en esa instancia**; no demuestran
atomicidad entre procesos, durabilidad ni comportamiento de transacciones
PostgreSQL. Una excepción simulada antes de publicar el estado deja intacta la
instantánea. La pérdida de respuesta se simula después de que una llamada haya
terminado, y el reintento usa el mismo ID; no reproduce fallos reales de red.

El experimento usa `ConversationMemory` solo para validar el mensaje canónico;
no escribe en una instancia compartida de esa clase ni añade recuperación por
relevancia. Tampoco convierte mensajes libres o contenido generado por un
modelo en afirmaciones automáticamente. Un llamador que pase contenido no
confiable a `confirm` puede hacer que se atribuya bajo la política falsa del
laboratorio; en una aplicación real ese límite requeriría controles propios.

Antes de una integración persistente habría que definir autenticación y
autorización reales, migración de identificadores, política de retención,
permisos y pruebas transaccionales contra una base temporal. El
[diseño de procedencia](../../docs/provenance-design.md) describe esas
decisiones pendientes; esta demostración no las resuelve.
