# Bitácora de auditoría y errores

La aplicación genera JSON Lines: un evento por línea, con fecha ISO 8601, zona horaria y un `request_id` para relacionar la operación y sus errores. El registro se activa al iniciar el backend y se vacía al detenerlo normalmente.

## Archivos por fecha

```text
Qu
```

La fecha de carpeta y la hora de los eventos usan `America/Caracas` por defecto. Un evento a las 02:00 UTC del 14 de octubre queda en la carpeta del 13 de octubre. Los archivos se abren en modo append: reiniciar el backend conserva lo anterior. Solo se crean los archivos que reciben eventos.

Configuración: `LOGS_DIR=logs` y `LOGS_TIMEZONE=America/Caracas`. En Docker se usa `/app/logs`, con permisos del usuario de la aplicación.

## Qué se registra

- `http_request`: todas las peticiones HTTP que llegan al backend, incluidas lecturas y rechazos. Método, ruta del endpoint, código HTTP, duración, resultado y los identificadores UUID/numéricos de la ruta. La ruta registrada es la plantilla del endpoint, sin query string.
- `database_change`: altas, modificaciones y eliminaciones del ORM después del commit. Incluye tabla, identificador y nombres de campos modificados. Las operaciones masivas incluyen tabla, tipo de operación y filas afectadas cuando el driver informa ese número. Las transacciones revertidas y los savepoints revertidos no se publican como confirmados.
- `domain_error`, `validation_error`, `http_error`, `unexpected_error`: códigos, tipo de excepción y ubicación de la pila cuando corresponde. Los errores inesperados se relacionan con el `X-Request-ID` que recibe el cliente.
- `client_error`: errores JavaScript y promesas no manejadas del frontend autenticado. Solo tipo y área, con un máximo de cinco reportes por minuto en cada instancia del frontend.
- `application_started`, `application_stopped`: ciclo de vida del backend. Los demás mensajes del logger `app` conservan nivel y origen; si contienen una excepción, se conserva su tipo y pila, sin su mensaje.

Las peticiones autenticadas registran `user_id`, `filial_id` y `holding_id` cuando corresponde. Un login correcto identifica al usuario; los intentos fallidos y las solicitudes anónimas pueden no tener actor. Los trabajos automáticos sin usuario tampoco tienen actor.

No se registran cuerpos de petición, query strings, cookies, tokens, contraseñas, correos, mensajes de excepciones ni valores anteriores/nuevos de campos. Un cambio de teléfono indica que cambió el campo, sin copiar el teléfono a los logs.

## Reportes desde el móvil

Endpoint autenticado: `POST /api/v1/telemetry/errors`, tag **Bitácora**. Ejemplo:

```json
{
  "platform": "mobile",
  "kind": "unhandled_rejection",
  "error_type": "TypeError",
  "area": "service_orders"
}
```

Respuesta: 204. Los valores permitidos aparecen en OpenAPI. El móvil debe integrar esta llamada para registrar sus errores locales; sus peticiones al backend ya están registradas. No enviar mensajes, pilas, tokens ni IDs de usuario: el endpoint rechaza campos adicionales y obtiene la identidad de la autenticación. En el frontend web ya hay listeners conectados; los fallos al reportar no generan una recursión de reportes.

## Despliegue y lectura

El Compose de producción monta `backend_logs:/app/logs`. Recrear el contenedor conserva el volumen; eliminar ese volumen elimina la bitácora. Si se usa otro Compose en EC2, añadir ese montaje y dar permisos de escritura al usuario `app`.

Ejemplo, sustituyendo `NOMBRE_DEL_BACKEND` por el nombre del contenedor:

```sh
docker exec NOMBRE_DEL_BACKEND cat /app/logs/2026-10-13/audit.jsonl
docker exec NOMBRE_DEL_BACKEND cat /app/logs/2026-10-13/errors.jsonl
```

No requiere migración de base de datos. Publicar backend y frontend para disponer también de los reportes del navegador. No se eliminan automáticamente los históricos: planificar respaldo y espacio del volumen según el uso.

## Alcance

Esta es una bitácora operativa en archivos, no un registro inmutable. No guarda cambios hechos directamente en SQL fuera de la aplicación ni actividad local de un móvil que no reporte errores. La escritura ocurre en una cola de proceso para evitar bloquear cada petición; el apagado normal vacía la cola, pero una caída abrupta puede perder eventos todavía pendientes. Si falla el disco o sus permisos, se emite una alerta a stderr sin impedir la operación de negocio. Los archivos no se exponen como recursos públicos de la API.
