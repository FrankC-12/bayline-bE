# Reintentos de cobros y movimientos de inventario

Los endpoints siguientes aceptan un `request_id` UUID:

- `POST /api/v1/part-sales/{sale_id}/payments`
- `POST /api/v1/almacen/stock-in`
- `POST /api/v1/almacen/stock-out`

Un cliente genera el UUID al enviar una operación nueva y conserva el mismo UUID y el mismo cuerpo para reintentar si pierde la respuesta. Las peticiones simultáneas con la misma clave se serializan en PostgreSQL. El efecto de negocio y su respuesta se confirman en una sola transacción.

Repetir la clave con los mismos datos devuelve la respuesta guardada, sin nuevos cobros ni movimientos. Cambiar datos, venta, usuario, filial o tipo de operación conservando la clave devuelve 409, código `operation_request_conflict`. Una operación fallida no consume la clave. Una operación nueva e intencional, aunque sus datos sean idénticos, debe usar otra clave.

La respuesta del reintento representa el momento original del guardado; consultar la venta o el inventario después para obtener el estado actual. El servidor registra las respuestas en `operation_receipts`, sin vencimiento automático, y un evento de auditoría `operation_replayed` cuando se reutiliza una operación confirmada.

Ejemplo de abono:

```json
{
  "request_id": "362299b8-af13-478f-8bd9-c6e3150af388",
  "payment_method": "usd",
  "paid_usd": "50",
  "paid_bs": "0",
  "usd_account_id": "839c1619-e8a4-419b-83bf-39599e480798"
}
```

El frontend web ya envía las claves desde los formularios de cobro de repuestos y entradas/salidas manuales. Mantiene la clave mientras el mismo formulario está montado y la conserva después de un error; editar los datos genera otra clave. Al guardar correctamente, inicia una clave nueva para la próxima operación. Cerrar o recargar el formulario puede perder la clave local: comprobar el estado antes de volver a registrar una operación cuyo resultado sea incierto. El móvil debe integrar el mismo contrato y conservar sus claves de reintento.

Por compatibilidad, `request_id` sigue siendo opcional. Las llamadas sin clave no tienen esta protección de reintento; el bloqueo del saldo evita sobrecobros, pero no identifica un abono repetido que todavía cabe en el saldo.

Las facturas de ODS y sus cobros conservan sus mecanismos existentes de `request_id` y validación. Los despachos de taller y la confirmación de transferencias completadas conservan la protección por transición de estado.

## Despliegue

Publicar el backend y ejecutar `alembic upgrade head` (revisión `e2a5c81b7d40`) antes de publicar el frontend. No requiere seed. Esta etapa cubre los tres endpoints descritos; otros flujos de creación, como carga masiva, devoluciones y apertura de ventas, no adquieren una clave automáticamente.
