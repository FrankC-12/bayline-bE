# Permisos por recurso — inventario y Clientes

Las mutaciones públicas de inventario validan dos niveles antes de escribir: permiso de edición de Almacén en la filial solicitada y pertenencia de cada almacén/repuesto a esa misma filial.

La comprobación por lotes se aplica a entradas, salidas, creación de transferencias, cambios de estado de transferencias, cambio de ubicación y recepción masiva. La revisión de una carga masiva valida el almacén destino. Si una entrada contiene una línea válida y otra de una filial ajena, la petición completa se rechaza antes de crear lotes o movimientos.

Los recursos de otra filial responden 403 con código `resource_filial_mismatch`, sin devolver sus nombres ni otros detalles. Un ID inexistente devuelve 404. Declarar una filial propia en el cuerpo no permite operar sobre IDs de otra filial.

Estas validaciones se realizan en los endpoints públicos mediante `AlmacenService.ensure_inventory_scope`; las llamadas internas de servicios siguen siendo código de confianza. Esta revisión se centra en las referencias de almacenes y repuestos; no sustituye una revisión completa de todos los módulos o referencias adicionales de compras.

En la pantalla de Clientes, el permiso `clientes-vehiculos: ver` permite abrir una ficha en consulta: los campos quedan deshabilitados y no aparecen Guardar ni Nuevo cliente. Con `editar`, las acciones vuelven a estar disponibles. El backend mantiene sus verificaciones de edición independientemente de lo que muestre la interfaz.

No hay migraciones ni seed nuevos en esta entrega. Publicar backend y frontend para aplicar ambas capas. Las migraciones pendientes de entregas anteriores conservan su necesidad de despliegue.
