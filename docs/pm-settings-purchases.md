# Ajustes, garantías y compras — integración del rediseño

Todos los endpoints usan el prefijo `/api/v1`, autenticación existente y validación de filial/holding. Swagger agrupa la configuración y los temparios bajo **Ajustes**. Compras y Finanzas conservan sus etiquetas funcionales.

## Ajustes

- `GET /settings?filial_id=UUID`: configuración, modo BCV y `field_changes` con usuario/fecha por valor.
- `PATCH /settings?filial_id=UUID`: actualización parcial; enviar únicamente los campos cambiados de la sección. Requiere Ajustes EDITAR.
- `POST /settings/bcv/restore?filial_id=UUID`: restaura la última tasa oficial almacenada y el modo automático; falla si no existe una tasa oficial disponible. No dispara una consulta externa.
- `/labor-settings` permanece por compatibilidad.

Ejemplo de sección de mano de obra:

```json
{"hourly_rate":25,"commission_percentage":30}
```

Enviar `bcv_rate` fuerza el valor manual; permanece hasta restaurar el oficial. Impuestos: `iva_percentage`, `igtf_percentage`. Retenciones: `iva_retention_default_percentage`, `islr_retention_default_percentage`. Soporte: `manual_movement_attachment_threshold_usd`. Garantías: `workshop_warranty_days`, `workshop_warranty_km`, `part_warranty_days`, `vehicle_warranty_default_months`.

El frontend tiene once secciones, aviso de cambios sin guardar y metadatos de edición. Postventa deja de aparecer como módulo; sus rutas antiguas redirigen. Las pantallas de políticas, planes y tipos de ODS se retiran. Los datos históricos y las API heredadas se conservan; las operaciones obsoletas se ocultan del esquema público.

Los catálogos existentes conservan sus rutas. Se agregan `GET/POST /stock-out-reasons` y `PATCH /stock-out-reasons/{id}` con nombre, estado activo y semántica de salida/devolución. Son compartidos por holding; las modificaciones requieren Ajustes EDITAR. Las salidas de almacén aceptan `reason_id` y conservan el texto del motivo en el movimiento.

## Temparios y órdenes

`GET/POST /temparios`, `GET/PATCH /temparios/{id}` y `POST /temparios/{id}/duplicate`. La administración consulta `include_inactive=true`. La edición permite estado activo, compatibilidad, herramientas, repuestos y garantía `standard`, `custom` o `none`; custom requiere días o kilómetros positivos. Las garantías heredadas diferentes se mantienen hasta que el usuario elija reemplazarlas.

Precio: costo de repuestos + margen del 30 % + horas estimadas × tarifa = subtotal; después IVA. Comisión: porcentaje configurado × mano de obra facturada estimada, sin usar el cronómetro real.

Las ODS nuevas guardan una copia de impuestos, mano de obra y comisión. Editar Ajustes no recalcula las ODS existentes. La tasa cambiaria operativa se consulta al facturar/pagar. El tipo de ODS se deriva del origen regular, reclamo/retrabajo, garantía de fábrica o campaña; no se ofrece selector en la creación normal.

El cierre de una ODS acepta `exit_mileage`; debe ser al menos el kilometraje de entrada. Las nuevas órdenes con kilometraje de entrada requieren el de salida para cerrar. Las garantías se generan al cerrar y conservan sus términos, fecha y kilometraje de vencimiento. Las piezas requieren instalación/consumo registrado. Vence el primero entre fecha y kilómetros.

## Ficha del vehículo

- `GET /clients/vehicles/{id}/service-history?page=1&size=30`: historial paginado de ODS del VIN dentro de la filial, con servicios, importe y kilometraje.
- `POST /clients/vehicles/{id}/factory-warranty`: formulario multipart desde la ficha; `starts_at`, `duration_months`, `duration_km`, `note` y `certificate` opcional. Debe proporcionarse al menos una cobertura positiva. Requiere Clientes EDITAR; vehículo con VIN, sin garantía previa y sin venta registrada por el concesionario.
- Las ventas de vehículos nuevos generan la garantía de fábrica automáticamente. Una venta usada no reinicia una garantía existente.

La ficha reúne garantía de fábrica, garantías del taller, historial y recomendaciones. Los reclamos siguen el flujo de autorización existente.

## Compra: solicitud → recepción → conciliación → pago

1. Crear/consultar la solicitud con los endpoints existentes `/purchase-requests`.
2. Almacén EDITAR: `POST /purchase-requests/{id}/receive` recibe cantidades y ubicación por línea. Cada recepción crea lotes al costo cotizado. Las recepciones parciales dejan pendiente lo faltante.
3. Administración EDITAR: `POST /purchase-requests/{id}/reconcile` registra factura, control, fecha, crédito, IVA y costo facturado de todas las líneas. Corrige el costo de los lotes vinculados y genera el saldo por pagar.
4. Finanzas Egreso EDITAR: `POST /purchase-requests/{id}/payments` registra abonos y crea el egreso en la cuenta elegida. Solo queda pagada al liquidarse el saldo.

Recepción:

```json
{"request_id":"UUID-del-intento","warehouse_id":"UUID","lines":[{"line_id":"UUID","quantity":5,"location":"Pasillo 3"}]}
```

Conciliación:

```json
{"invoice_number":"1232","invoice_control_number":"3232","invoice_date":"2026-10-14","credit_days":30,"iva_percentage":16,"lines":[{"line_id":"UUID","unit_cost":5}]}
```

Pago:

```json
{"request_id":"UUID-del-intento","account_id":"UUID","currency":"usd","amount":5,"entry_date":"2026-10-14","reference":"ABONO-1"}
```

Para bolívares: `currency="bs"`, importe en Bs y `exchange_rate` positivo; el saldo se descuenta por su equivalente USD. La cuenta debe estar activa, pertenecer a la filial y coincidir con la moneda. No se admiten sobrepagos ni pagos sin conciliación. Reutilizar el mismo `request_id` para reintentar la misma recepción o pago; un intento distinto requiere otra clave.

El detalle devuelve recibidos/lotes por línea, factura, vencimiento, monto pagado y actividad con usuario y fecha. Las CxP se ordenan por vencimiento y muestran las compras históricas sin factura con “Conciliar primero”. No se permite marcar una solicitud como conciliada/pagada mediante el cambio manual de estado.

Los pagos históricos solo se recuperan al conciliar cuando existe evidencia suficiente en los egresos vinculados. Una asignación agrupada ambigua exige revisión; el sistema no inventa un reparto.

## Lotes y transferencias

Las transferencias consumen lotes de origen en FIFO y crean un lote destino por cada lote consumido, con su mismo costo unitario. Se conservan referencias de origen/destino y compra. El detalle del lote muestra procedencia, proveedor/factura cuando existen y salidas relacionadas. Los consumos históricos mantienen sus costos registrados.

## Despliegue y límites históricos

1. Respaldar la base de datos y coordinar el despliegue de backend y frontend.
2. Ejecutar `alembic upgrade head` en el entorno del backend antes de activar esta versión.
3. Reiniciar servicios y renovar la sesión para cargar el catálogo de permisos.

Migración principal: `b5d10c73a982_settings_purchase_lineage.py`. Copia permisos de Postventa hacia Ajustes cuando no existe una configuración explícita de Ajustes. Conserva los datos previos.

Las ODS antiguas sin copia de configuración reciben los últimos valores conocidos disponibles; no es posible reconstruir configuraciones históricas que nunca se almacenaron. Tampoco se reconstruye la procedencia FIFO de transferencias antiguas que promediaron costos sin registrar lotes. El downgrade estructural se prueba, pero valores nuevos de enum y copias de permisos se conservan; una reversión de aplicación requiere revisar los registros creados con esta versión.

## Verificación

- 711 pruebas generales aprobadas; 52 pruebas de integración ejecutadas por separado en PostgreSQL desechable.
- Migración completa y downgrade/upgrade de la última revisión verificados.
- Frontend: TypeScript, ESLint de vistas modificadas y 41 pruebas Node.
- Chromium con datos ficticios: edición y navegación de Ajustes, tempario, recepción/conciliación, abono y diseño móvil. No se utilizaron datos productivos.
