# Entrega para mobile: taller, 19 de octubre de 2026

Alcance: pedidos 2–18 de `pedido_backend_frank.pdf`. HTTPS/dominio queda fuera de este cambio. Las rutas siguientes llevan el prefijo `/api/v1`.

## Instalación y despliegue

```sh
venv/bin/python -m pip install -r requirements.txt
venv/bin/alembic upgrade head
```

La revisión nueva es `b7e29c6140fa`. Agrega evidencias y pausas de tareas, recepción del vehículo, incidencias de retiro, correcciones de kilometraje, sesiones renovables, dispositivos y cola de notificaciones. Actualiza el indicador de ODS completadas con repuestos pendientes y pausa cronómetros simultáneos históricos conservando el tiempo acumulado.

Los refresh tokens anteriores al despliegue no tienen sesión persistida: los usuarios deberán iniciar sesión nuevamente. No se reescriben motivos del cliente ni lecturas de kilometraje históricas; el texto previamente sobrescrito no se puede reconstruir automáticamente.

## 2. Dispositivos, avisos y Firebase

- `PUT /devices`: `{ "token": "<FCM token>", "platform": "android" }` (`ios` también). Devuelve `id`, `platform`, `enabled`. Repetir el mismo token no crea dispositivos duplicados.
- `DELETE /devices/{id}`: desactiva únicamente un dispositivo del usuario autenticado.
- `GET /notifications?limit=50`: bandeja del usuario autenticado; máximo 100 por consulta.
- `PATCH /notifications/{id}/read`: marca leído un aviso propio.

Eventos y destinatarios:

| `kind` | Evento | Destinatario |
|---|---|---|
| `tecnico_asignado` | Alta o cambio de asignación | Técnico asignado |
| `repuestos_listos` | Almacén completa el despacho | Técnico y asesor de la ODS |
| `upsell_identificado` | Se registra un hallazgo | Asesor de la ODS |
| `upsell_aprobado`, `upsell_rechazado`, `upsell_pospuesto` | El asesor registra la decisión del cliente sobre un hallazgo | Técnico de la ODS (en la aprobación, la ODS que recibe el trabajo) |
| `tarea_cerrada` | Tarea completada o cancelada | Asesor de la ODS |
| `orden_lista` | ODS pasa a completada | Asesor de la ODS |

Cada push lleva `notification_id`, `event`, `service_order_id`, `entity_id` y `filial_id` como strings para abrir la pantalla correspondiente. Mobile debe deduplicar por `notification_id`: un reinicio entre la aceptación de Firebase y el commit local puede repetir una entrega.

Los eventos se guardan dentro de la transacción del taller. Un worker procesa la cola con bloqueos `SKIP LOCKED`, reintentos y registro de los dispositivos ya entregados. No se envían eventos revertidos. Tokens inválidos se desactivan; no se escriben tokens ni mensajes del SDK en logs.

Configurar `PUSH_ENABLED=true`, `FIREBASE_PROJECT_ID` y una cuenta de servicio mediante `FIREBASE_CREDENTIALS_FILE` o las credenciales predeterminadas de Google. El SDK oficial usa `messaging.send`: https://firebase.google.com/docs/reference/admin/python/firebase_admin.messaging. Sin configuración los avisos quedan en la bandeja/cola y no se hacen envíos externos. En Compose la cuenta de servicio se monta de `deploy/secrets/` a `/run/secrets/`; no se debe incluir en Git ni en la imagen.

## 3–6. Motivo del cliente, garantías y kilometraje

- `customer_reason` en el alta de ODS contiene exclusivamente el motivo enviado por recepción. Las notas de la inspección no lo reemplazan. El portal mantiene el campo editable.
- La ficha, el contexto de reclamo y las garantías usan la lectura de la última visita, incluyendo una salida de ODS cerrada más reciente. Ya no se usa el máximo histórico para vencer garantías.
- Las respuestas de garantías incluyen `current_mileage` y `expiration_reason`: `null`, `fecha`, `kilometraje` o `fecha_y_kilometraje`. Los días se calculan en Caracas.
- `last_visit_date` y `current_mileage_visit_date` son fechas de Caracas, incluso si el timestamp UTC corresponde al día siguiente.
- Crear/editar una inspección con kilometraje inferior al último registro devuelve `409 mileage_inconsistent`. Una corrección legítima debe incluir `mileage_correction_reason` (3–1000 caracteres). Se conserva la lectura histórica y se guarda la justificación en la inspección.

## 7–8. ODT vacías y finalización de ODS

Se mantiene la política actual: el estado de trabajo de la ODS se deriva de las tareas. Si todas terminaron, la ODS puede estar `completado` aunque falte retirar repuestos. En ese caso `completed_with_pending_items=true`; recibir todos los repuestos limpia el indicador. Forzar completado sigue registrando quién lo hizo y cuándo.

Una ODT pendiente que queda sin líneas se conserva internamente, pero se excluye de los listados y del resumen y no cuenta como pendiente. No se eliminan despachos ni se cambian existencias para ocultar la ODT.

Flujo de estados compartido entre web y app:

1. El técnico termina o cancela las tareas. `completado` significa **trabajo finalizado**, no entrega del vehículo. Las ODT conservan su proceso de preparación/retiro.
2. El asesor gestiona la facturación y el cobro desde el portal. La entrega no exige usar la app.
3. Registrar entrega llama a `POST /service-orders/{id}/close`. La ODS pasa a `orden_cerrada`, sus ODT reciben `closed_at` y se limpia `completed_with_pending_items`.

Mientras la ODS siga editable, agregar o reabrir tareas recalcula su estado y limpia `completed_at` si vuelve a haber trabajo pendiente. Eliminar una tarea recalcula usando solamente las tareas restantes. Las órdenes facturadas o terminales mantienen sus restricciones de edición.

## 9–11. Técnico, inicio de tarea e incidencias de retiro

- `GET /bays?filial_id=...` permite lectura al técnico de su filial. No autoriza crear o editar bahías.
- `PATCH /service-order-tasks/{id}` con `status=en_progreso` y el arranque del cronómetro responden `409 task_parts_not_received` si los repuestos vinculados a esa tarea no tienen `picked_up_at`.
- `POST /service-order-transfers/{id}/not-received`:

```json
{ "reason": "faltante", "notes": "Falta el filtro de aceite" }
```

`reason`: `faltante`, `incorrecto`, `danado`, `otro`; `otro` requiere notas. Solo aplica a un despacho completado aún no recibido y requiere acceso a la ODS. El summary devuelve `receipt_incident` y `receipt_incident_history` con motivo, notas, fecha y usuario. Reportarlo no altera inventario ni vuelve a despachar piezas automáticamente. Después de corregir físicamente el problema, confirmar el retiro mediante `/pickup` limpia la incidencia activa y conserva su historial.

## 12. Fotos de retiro

`pickup_photo_url` es ahora una ruta autenticada: `/api/v1/service-order-transfers/{id}/pickup-photo`. Para S3 autoriza al usuario antes de generar un enlace de lectura de 5 minutos; para disco devuelve el archivo directamente. Mobile debe enviar su Bearer al solicitar la ruta y seguir la redirección si existe.

Las nuevas fotos se guardan fuera de `/uploads` o en `S3_PRIVATE_BUCKET_NAME`, que debe ser diferente del bucket público y tener **Block Public Access**. El fallback local usa `PRIVATE_UPLOADS_DIR`; Docker prepara la carpeta para el usuario de la aplicación y Compose conserva un volumen separado.

Las fotos antiguas ya públicas en S3 no se vuelven privadas por generar URLs firmadas. Antes de considerar cerrada la configuración del entorno se debe revisar el bucket desplegado, mover/restringir esos objetos y quitar su acceso público. No se cambiaron políticas de AWS ni se borraron objetos remotos. La configuración local revisada no tiene bucket S3; no permite confirmar el bucket del servidor de desarrollo. `UPLOADS_ENVIRONMENT` permite detectar que la configuración de almacenamiento pertenece a otro entorno.

## 13–14. Sesión y seguridad

Mobile envía `Authorization: Bearer <access_token>` y `X-CSRF-Protection: 1`. Si no existe `Origin`, el header de protección se acepta en todas las mutaciones; el middleware no sustituye la autenticación ni los permisos. Se conserva la validación de orígenes del portal y el header sigue siendo obligatorio.

- SQLAlchemy parametriza las consultas; contraseñas con bcrypt.
- Login admite correo de hasta 254 caracteres y contraseña de hasta 72 bytes.
- Error de correo inexistente y clave incorrecta: `401 invalid_credentials`.
- Límite compartido en DB por ventanas de 15 minutos: 10 intentos por correo y 100 por IP; exceso: `429 login_rate_limited`.
- Refresh tokens con identificador único y hash persistido. Cada refresh rota y revoca el anterior; dos renovaciones simultáneas no pueden consumirlo dos veces.
- `POST /auth/logout` acepta `{ "refresh_token": "...", "device_id": "..." }`. Revoca la renovación y, si se envía un dispositivo propio, desactiva sus pushes. En web se toma el refresh de la cookie.
- El access token ya emitido conserva su vencimiento; logout impide obtener otro a partir del refresh revocado.
- Cookies HttpOnly/SameSite y banderas Secure por entorno se conservan; no se modificó el despliegue de HTTPS.
- OpenAPI declara HTTP Bearer. Los logs no guardan cuerpos de login y SQL oculta parámetros.

## 15. Cronómetro

```http
POST /service-order-tasks/{id}/timer/pause
```

```json
{ "reason": "almuerzo", "notes": "Regreso a las 13:00" }
```

Motivos: `almuerzo`, `otra_tarea`, `esperando_repuesto`. `TaskRead` expone `pause_reason` y `pause_history`. El cuerpo sigue siendo opcional para los clientes web anteriores; mobile debe enviarlo.

Si otro cronómetro del mismo técnico está corriendo, start responde `409 technician_timer_running`, incluso si pertenece a otra ODS. No se permite cambiar de técnico mientras corre su cronómetro. Completar/cancelar una tarea y cerrar/cancelar una ODS detiene sus cronómetros y conserva el tiempo acumulado.

## 16. Recomendaciones aceptadas en recepción

- `GET /upsells/{id}/quote`: estimación vigente de mano de obra, repuestos, IVA y total. No modifica la cotización original ni reserva stock. `has_work=false` identifica recomendaciones históricas sin tareas/repuestos que requieren revisión manual.
- El `POST /service-orders` incorpora opcionalmente:

```json
{
  "accepted_upsells": [
    { "upsell_id": "<uuid>", "approval_channel": "presencial" }
  ]
}
```

El resto del payload de alta se conserva. La ODS se crea y las recomendaciones se aplican dentro de la misma transacción, a esa ODS, con sus precios vigentes. La aprobación conserva el usuario autenticado, el canal y el destino. No se puede aplicar una recomendación de otro vehículo/filial ni repetir una ya resuelta. Un error revierte la operación completa. Para una ODS existente sigue disponible `PATCH /upsells/{id}` con `target_service_order_id`.

## 17. Recepción, notas, fotos y tareas no realizadas

- `POST /service-orders/{id}/vehicle-received`: idempotente. Devuelve `vehicle_received_at` y `vehicle_received_by_user_id` en la ODS.
- `PATCH /service-order-tasks/{id}/notes`: `{ "notes": "Hallazgo o trabajo realizado" }`.
- `POST /service-order-tasks/{id}/photos`: multipart con `photos`; hasta 10 imágenes por tarea, con el límite de tamaño habitual por imagen.
- `PATCH /service-order-tasks/{id}`: `{ "status": "cancelada", "reason": "El cliente no autorizó el trabajo" }` para cerrar sin realizarla. `TaskRead` devuelve `cancellation_reason`, `notes` y `photo_urls`.

El técnico solo modifica sus órdenes asignadas. Se rechazan cambios a órdenes cerradas, canceladas o facturadas conforme a las reglas existentes.

## 18. Fechas del portal

Fechas `YYYY-MM-DD` se formatean como calendario con `formatEntryDate`, sin parsearlas como medianoche UTC. Se corrigieron los reclamos, sus vencimientos, garantías de fábrica, mantenimiento y la fecha de kilometraje de la ficha del vehículo. Los timestamps completos mantienen su conversión a la zona de Caracas.

## Cierre automático de ODT al entregar la ODS

Desde la revisión `d5e820b91c43`, cerrar/registrar entrega de una ODS cierra también todas sus ODT, en la misma transacción. El backend registra `closed_at` y `closed_by_user_id` en cada ODT. La fecha y el usuario se incluyen en el resumen de la ODS y en las solicitudes de Almacén.

- `closed_at != null` identifica una ODT cerrada. La app debe detener su contador y deshabilitar acciones operativas; el backend también las rechaza.
- Se mantienen los valores existentes de `status` para conservar compatibilidad y la evidencia del proceso físico. Cerrar no significa despachar ni retirar: `stock_deducted`, `completed_at`, `picked_up_at` y las fotografías conservan sus datos reales.
- En la bandeja de Almacén una solicitud cerrada pasa al historial (`stage=completado`) y deja de contar como pendiente, aunque no hubiese sido despachada. La web muestra **Cerrada**.
- Se liberan las reservas de stock no consumido y se cancelan los traslados vinculados que todavía no se habían recibido. No se crean salidas de inventario por el cierre.
- La migración corrige también las ODT de ODS ya cerradas. No atribuye un usuario histórico que no estuviera registrado.
- La instantánea de precios/factura permanece intacta; el resumen añade los metadatos operativos de cierre al consultar.
