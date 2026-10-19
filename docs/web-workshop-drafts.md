# ODS web: borradores, inspecciones y seguimiento

## Instalación

Aplicar `alembic upgrade head` antes de publicar el backend y el frontend juntos. La revisión `c4d719a80b32` agrega borradores de ODS y trazabilidad de decisiones de upsells/reclamos. No convierte las ODS existentes en borradores.

## Borradores web y app

- `POST /api/v1/service-order-drafts`: recibe `filial_id` y opcionalmente `vehicle_id`, `scheduled_at`, `bay_id`, `upsell_id`. Devuelve una ODS con número propio, `is_draft=true`, `creation_source=web` y `draft_revision=1`. Puede estar sin vehículo.
- `PATCH /api/v1/service-order-drafts/{id}`: guarda vehículo, técnico, bahía, motivo y horario. Enviar `expected_revision` para detectar otra ventana editando el mismo borrador; el conflicto responde 409. La UI guarda automáticamente.
- `POST /api/v1/service-order-drafts/{id}/submit`: exige vehículo, técnico activo con permiso de técnico y al menos una tarea vigente. Cambia `is_draft=false`, registra `submitted_at` y encola la notificación al técnico. No exige inspección preliminar ni mueve inventario.
- `DELETE /api/v1/service-order-drafts/{id}`: descarta únicamente un borrador no enviado, incluidos tareas, líneas y cotizaciones de lotes. No consume inventario.
- Las tareas traen sus repuestos del tempario a una ODT pendiente, sin pedir. Al quitar una tarea de un borrador se quitan también sus repuestos automáticos.
- `GET /api/v1/service-orders` y `/service-orders/page` excluyen borradores por defecto. La web usa `include_drafts=true`. Las listas del técnico siempre los excluyen aunque envíe ese parámetro, y el técnico no puede abrir el detalle de un borrador.
- Se mantiene `POST /api/v1/service-orders` para el alta móvil con inspección. `status` mantiene sus valores existentes; no necesita un nuevo enum móvil. El cliente puede usar `workshop_stage` y `estimated_hours` para el calendario.
- Los borradores no permiten iniciar tareas, registrar hallazgos ni solicitar/despachar repuestos. La agenda conserva horas UTC y las presenta en Caracas. Se detectan choques al asignar horarios a borradores.

## Registros de inspección

`GET /api/v1/service-orders/{id}/inspection-records` devuelve `preliminary` y `detailed` (o `null`), con todos los campos, daños, fotografías, datos de autoría y anexos. Puede consultarse después del cierre. En la web, pulsar el título de cualquiera de las dos tarjetas abre su registro completo.

## Upsells

La web tiene Por preguntar, Para después e Historial; no crea hallazgos. El técnico asignado con permiso `tecnico-servicio:editar` usa el endpoint existente `POST /service-orders/{id}/upsells`; el servidor toma al usuario autenticado como autor. El asesor registra Sí / Después / No. Las decisiones nuevas guardan `resolved_by_user_id` y `resolved_at`; se conserva la cotización histórica y se consulta `/upsells/{id}/quote` para la estimación actual.

Crear una ODS desde una recomendación aplazada copia su trabajo al borrador. No la aprueba al abrir el borrador; se resuelve al enviarlo al taller, siempre que conserve el trabajo propuesto y no haya sido resuelta desde otra orden. Las recomendaciones sin trabajo cotizable se muestran para revisión, sin un precio cero ficticio.

## Reclamos

- `POST /warranty-claims/from-warranty` (multipart): vehículo, una garantía del taller/fábrica o campaña, síntoma, kilometraje, fotos y documentos opcionales. Fecha automática. La garantía debe pertenecer al vehículo y estar vigente con ese kilometraje; tipo, servicio y ODS se derivan en servidor.
- `POST /warranty-claims/{id}/review`: autorización de Administración (jefe/gerente). Un comeback aprobado crea la ODS de retrabajo a cargo del taller. Fábrica/campaña pasan a `brand_status=en_marca`, sin abrir todavía una ODS.
- `POST /warranty-claims/{id}/brand-response`: registra la respuesta de la marca. Su autorización crea la ODS a cargo de fábrica. Rechazar exige `rejection_reason` y registra quién/cuándo.
- `GET /warranty-claims/overview`: pendientes, costo directo de retrabajos convertidos este mes (repuestos despachados y mano de obra completada a tarifa congelada), cantidad de retrabajos y tasa sobre ODS cerradas del mes.
- Desde un comeback convertido, `/installed-parts` lista repuestos despachados de la ODS original; `POST /supplier-claim?part_id=...` crea el reclamo al proveedor con trazabilidad al lote y la compra. Reintentar no duplica el reclamo de un mismo lote.
- Los endpoints anteriores se conservan para contratos existentes. El nuevo flujo web utiliza las rutas de selección de garantía, revisión y respuesta de marca.

Bahías se administran en **Ajustes > Taller > Bahías**, con permisos de Ajustes. Asesores y técnicos mantienen acceso de lectura al calendario.
