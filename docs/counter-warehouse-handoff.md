# Ventas de mostrador: despacho y recepción separados

## Etapas y permisos

1. Crear la venta deja `status=pendiente`: «Esperando despacho de almacén». El vendedor recibe el aviso de espera y los usuarios con permiso de edición de almacén reciben la solicitud.
2. Solo `almacen:editar` puede enviar `PATCH /part-sales/{id}` con `status=pedido` y `dispatched_lines`. Debe confirmar todas las líneas, una vez cada una, con cantidades iguales a las solicitadas. Se guardan `dispatched_at` y `dispatched_by_user_id`.
3. Al despachar se notifica a mostrador: el asesor confirma que recibió los repuestos enviando `status=completado`. Requiere `repuestos:editar`; no puede hacerlo un usuario con rol `almacenista` ni el mismo que despachó, salvo el súper administrador de filial. Se guardan `received_by_user_id` y `completed_at`.

El rol sembrado de almacén tiene edición de catálogo de repuestos: ese permiso no le permite confirmar su propio despacho como recibido. La separación también se verifica al llamar directamente al endpoint.

Los usuarios con lectura de almacén pueden consultar el pedido de mostrador y sus nombres/códigos de repuestos sin necesitar acceso al catálogo completo. La pantalla de almacén no expone acciones de recepción ni cobro. La del vendedor no expone despacho.

## Pantallas y avisos

- Almacén abre el detalle desde la pestaña Mostrador en `/dashboard/almacen/mostrador/{id}`. Al volver conserva la pestaña Mostrador.
- El vendedor consulta `/dashboard/repuestos/ventas/{id}` y ve la espera o la acción de recepción, según el estado confirmado por el servidor.
- La campana del portal muestra la bandeja de notificaciones y permite abrir la solicitud. Se actualiza cada 30 segundos mientras la pestaña está visible; el detalle abierto de una venta pendiente se consulta cada 15 segundos.
- Las notificaciones se guardan con la transacción de la venta/despacho. No dependen de la disponibilidad de FCM para aparecer en la bandeja.
- Avanzar el flujo actualiza el detalle con la respuesta del servidor, sin vaciar la pantalla para volver a cargarla.

## Inventario, cobros e historial

Se mantiene el cálculo y la asignación FIFO existentes: el despacho y sus reintentos no vuelven a descontar inventario. La recepción no registra un cobro automáticamente. El cobro conserva sus permisos y flujo separados.

Una venta antigua en `pedido` sin `dispatched_at` requiere que almacén confirme el despacho antes de recibirla. No se inventan fechas ni responsables de registros históricos. Las ventas ya completadas se conservan.

## Despliegue

Desplegar backend y frontend, aplicando `alembic upgrade head` con la imagen actualizada. Migración `f3ca26d84104`, posterior a `e2b915cf7303`. No requiere variables de entorno nuevas.
