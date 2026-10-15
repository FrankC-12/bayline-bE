# ODS: vista del asesor con siete etapas

La ficha sigue la referencia del PM: encabezado con vehículo, placa, cliente, técnico y bahía; barra de etapas; inspecciones; tareas y hallazgos junto a repuestos; total del cliente al pie. Las acciones de forzar completado y cancelar están en el menú del encabezado; eliminar ODT está en su menú y conserva la restricción a pendientes.

Las etapas se calculan con datos persistidos. No hay botones para avanzarlas manualmente:

1. Preliminar: inspección vinculada.
2. Tareas: hay tareas asignadas.
3. Recepción: preliminar vinculada y todas las ODT tienen retiro confirmado. Una orden sin repuestos no exige una ODT vacía.
4. Minuciosa: estado `completada` del formulario del técnico.
5. Trabajo: todas las tareas terminadas o canceladas, con al menos una tarea.
6. Facturación y cobro: factura emitida y sin saldo pendiente en cuentas por cobrar.
7. Entrega: orden cerrada mediante el flujo existente, conservando kilometraje de salida y próxima visita.

La web consulta los avances técnicos; no ofrece iniciar/pausar cronómetros, cambiar estados de tareas, completar la minuciosa ni registrar el retiro de repuestos. Esas operaciones siguen disponibles para la app mediante sus endpoints existentes. El asesor puede agregar tareas y repuestos, asignar técnico/bahía, editar agenda, gestionar hallazgos, facturar y registrar entrega.

## Hallazgos

Se muestran bajo Tareas con descripción, importe y enlaces a fotografías:

- Sí: confirma medio de aprobación y agrega trabajo a la ODS actual.
- Después: persiste `pospuesto`; continúa en las recomendaciones del vehículo.
- No: persiste `rechazado` con motivo `cliente_no_lo_quiere`.

Cada aprobación crea una ODT adicional para sus repuestos y los incluidos en sus temparios; conserva las ODT anteriores. Si el trabajo no requiere piezas, no deja una ODT vacía. La aprobación, tareas y repuestos se confirman en una transacción. Un bloqueo de la recomendación impide duplicarla por aprobaciones simultáneas.

## API y compatibilidad

Se reutilizan `GET /detailed-inspections/by-order/{id}`, `GET /upsells`, `PATCH /upsells/{id}`, resumen de ODS, cuentas por cobrar, factura y cierre. No se agregan endpoints ni migraciones. El mapeo ORM del medio de aprobación se ajustó a los valores en minúscula definidos por la migración existente de PostgreSQL.

Se conserva el desglose histórico y el precio congelado de las facturas. La tarjeta muestra precio normal de repuestos, descuento, mano de obra, subtotal, IVA y total, e IGTF cuando corresponde. Las órdenes facturadas, canceladas y cerradas mantienen sus bloqueos.
