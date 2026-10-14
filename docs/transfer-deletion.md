# Eliminación de ODT

Endpoints autenticados (documentados en los tags existentes de ODS y Almacén):

- `DELETE /api/v1/service-orders/{order_id}/transfers/{transfer_id}`: exige que la ODT pertenezca a la ODS indicada.
- `DELETE /api/v1/service-order-transfers/{transfer_id}`: elimina la misma solicitud por su ID.
- `DELETE /api/v1/almacen/transfers/{transfer_id}`: elimina un traslado independiente.

Responden `204` sin cuerpo. Exigen permiso EDITAR en el módulo y filial del recurso. IDs inexistentes o de otra ODS producen `404`; falta de permiso, `403`.

Las solicitudes pendientes o enviadas pueden eliminarse mientras no hayan descontado stock. Sus líneas y reservas desaparecen junto con los traslados automáticos no recibidos. Los traslados ya recibidos conservan su historial y se desvinculan de la solicitud eliminada. No se modifica el stock físico ni se crean movimientos inversos.

Una solicitud despachada (incluidas solicitudes antiguas con stock ya descontado) produce `409 transfer_already_dispatched`. Un traslado recibido produce `409 transfer_already_received`. Un traslado automático asociado a ODS produce `409 transfer_linked_to_order`: eliminar su solicitud de origen, no una parte aislada del flujo. Las ODS cerradas, canceladas o facturadas mantienen sus restricciones de edición.

Los bloqueos de filial y recurso serializan la eliminación con el despacho y la recepción. Todo el cambio se confirma en una transacción. Una segunda eliminación del mismo ID devuelve `404`. La bitácora registra `transfer_deleted` y los cambios de base de datos, con usuario y fecha del contexto de la petición, sin registrar cuerpos privados.

Frontend: botón “Eliminar ODT” en el detalle de ODS y “Eliminar” en los traslados independientes de Almacén; confirmación previa, bloqueo durante la petición, errores visibles y recarga de las listas y del resumen. Las acciones se ocultan sin permiso de edición y cuando el despacho o recepción ya ocurrieron. No necesita migración.
