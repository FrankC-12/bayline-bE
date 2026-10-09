# Solicitudes del taller y despachos

El asesor prepara una ODT y revisa la disponibilidad antes de enviarla. Enviar cambia la solicitud a `PEDIDO`, reserva los lotes disponibles y no genera salidas físicas ni disminuye `Part.stock_quantity`. Las reservas enviadas permanecen vigentes hasta el despacho; una ODS cancelada deja de reservar stock.

La filial tiene un almacén de taller predeterminado. La migración selecciona el almacén activo más antiguo; se puede cambiar desde la bandeja del almacén. Los repuestos disponibles en otro almacén generan un traslado automático hacia el almacén de taller. Recibir ese traslado mueve los lotes y sus reservas entre almacenes, sin disminuir el inventario total de la filial.

El almacenista inicia la preparación y completa el despacho. Completar valida que los traslados hayan llegado y que exista la cantidad completa solicitada, descuenta FIFO, registra la salida y actualiza el inventario una sola vez. No hay despacho parcial: los faltantes quedan pendientes y aparecen en Compras. El retiro se confirma desde la ODS con una foto, independientemente de cuándo se completan las tareas.

## API

- `GET /service-order-transfers/{id}/request-preview`: destino, disponibilidad y faltantes antes de enviar.
- `POST /service-order-transfers/{id}/mark-ordered`: reserva y envío.
- `POST /almacen/service-order-requests/{id}/start?filial_id=...`: inicio de preparación.
- `POST /almacen/service-order-requests/{id}/complete?filial_id=...`: despacho y descuento físico.
- `POST /service-order-transfers/{id}/pickup`: retiro con archivo multipart `photo`.
- `GET /almacen/workshop-backorders?filial_id=...`: faltantes para Compras.
- `PATCH /almacen/warehouses/{id}` con `is_workshop_default: true`: asignación del almacén de taller.

Los permisos de almacén controlan preparación y despacho. El retiro requiere editar la ODS como asesor o como técnico asignado. Cada operación verifica la filial del recurso. Las reservas se respetan también en ventas y otras salidas de inventario.

## Publicación y compatibilidad

Publicar el backend y ejecutar `alembic upgrade head` antes de publicar el frontend. La revisión `c9f672a4d813` marca las solicitudes antiguas `PEDIDO`/`COMPLETADO` como ya descontadas para evitar una segunda salida. Para las solicitudes nuevas se registran los tiempos de envío, preparación, despacho y retiro y los usuarios responsables.

La bandeja separa espera, preparación y retiro. El promedio del almacén termina en el despacho y excluye el tiempo de retiro. Los tiempos de solicitudes antiguas que no tenían una etapa registrada no pueden reconstruirse.

## Verificación

`DEBUG=false venv/bin/python -m pytest tests -q` ejecuta las pruebas locales. `venv/bin/python scripts/test_postgres.py` crea una base PostgreSQL temporal, verifica migraciones y ejecuta pruebas de concurrencia, FIFO, traslados, permisos y retiro por HTTP.
