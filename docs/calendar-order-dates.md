# Calendario y fechas de ODS

## Corrección

El formulario normal de ODS enviaba su campo «Fecha de inicio» como `promised_at`, aunque el calendario consulta `scheduled_at`. El detalle también presentaba `promised_at` como inicio, además de mostrarlo como entrega. La agenda consultaba exclusivamente órdenes activas, ocultando visitas al cerrar las órdenes.

Ahora:

- `scheduled_at` guarda la fecha y hora de inicio de visita elegidas. El formulario normal usa la misma conversión de Caracas que el formulario del calendario.
- `promised_at` es una entrega prometida independiente, opcional, sin valor automático. En la creación puede omitirse o ser null. La conversión de un reclamo tampoco inventa una promesa usando la hora actual.
- `PATCH /api/v1/service-orders/{id}` permite `promised_at` o `clear_promised_at: true`, además de la modificación existente de `scheduled_at`. Conserva la autorización y las restricciones de edición de la ODS.
- La ODS incluye «Editar agenda y entrega prometida». Guardar una entrega no mueve la visita; quitarla deja el inicio intacto.
- `GET /api/v1/service-orders?filial_id=UUID&view=all&date=YYYY-MM-DD` consulta visitas de ese día de Caracas, incluidas las históricas, con límites `[00:00, 00:00 del día siguiente)`, independientes de la zona horaria de PostgreSQL.
- El calendario actualiza sus datos al crear/reagendar y consulta periódicamente el día elegido. La lista «Sin agendar» sigue usando las órdenes activas.
- El detalle muestra inicio y entrega desde sus campos correctos y fija la presentación horaria en America/Caracas.

## Datos anteriores

No se convierten automáticamente las promesas antiguas en citas ni se cambia una hora histórica: no hay evidencia suficiente para saber cuáles eran inicios y cuáles eran compromisos de entrega. Una orden antigua sin `scheduled_at` aparece en «Sin agendar»; desde su detalle se puede registrar el inicio correcto. Las horas de entrega equivocadas pueden corregirse o quitarse desde ese mismo formulario, mientras la orden sea editable.

No requiere migración de base de datos. Desplegar backend y frontend juntos porque la entrega pasa a ser opcional en el contrato de creación.

## Validación

711 pruebas generales, 54 pruebas de integración PostgreSQL y 41 pruebas Node aprobadas. Nuevas pruebas cubren el cambio de día a las 04:00 UTC (medianoche Caracas), órdenes cerradas, modificación de entrega sin mover la visita y eliminación independiente de la entrega. TypeScript y ESLint de archivos modificados aprobados.

Chromium con datos ficticios verificó citas activas/cerradas visibles, entrega inicialmente vacía, guardado de 16:30 Caracas como 20:30 UTC, eliminación de entrega, conservación del inicio de las 09:30 y diseño móvil sin desbordamiento.
