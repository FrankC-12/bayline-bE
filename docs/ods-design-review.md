# ODS: mejora de presentación y propuesta para el PM

## Cambios implementados

- Indicadores globales del tablero: órdenes activas, sin técnico, fecha prometida vencida y trabajo completado. Los filtros no alteran estos totales.
- Búsqueda por código, placa, marca/modelo y cliente; filtros por técnico y atención requerida, con cantidad visible y opción de limpiar.
- Se conservan las tres etapas operativas y la navegación a la orden. Sin asignación o con promesa vencida se considera atención requerida; no se infieren faltantes de stock desde el listado.
- Las tarjetas muestran la promesa de entrega, señales de atención y el próximo paso orientativo. Los cronómetros conservan días, horas, minutos y segundos.
- El detalle explica el siguiente paso y permite saltar a tareas, repuestos/ODT y resumen/coberturas. Facturación, cobro y cierre mantienen sus acciones y validaciones existentes.

## Propuestas para decidir con el PM

1. Separar visualmente avance del taller y avance administrativo. Una ODS con trabajo completado puede seguir pendiente de facturación, cobro o cierre; mostrar estos hitos sin crear estados nuevos arbitrarios.
2. Agregar una vista de lista compacta para días de alto volumen, compartiendo los filtros del tablero. Definir las columnas prioritarias con asesores antes de reemplazar la vista actual.
3. Incorporar una cola de bloqueos reales: repuesto pendiente, aprobación del cliente, inspección o asignación. Requiere datos explícitos del backend; no deducirlos solo por antigüedad.
4. Revisar creación guiada en tres pasos: vehículo/inspección, motivo/responsable/promesa y confirmación. Conservar el borrador al volver a un paso y no cambiar las reglas de origen/tipo de ODS.
5. Reunir actividad relevante en una línea temporal: recepción, asignación, tareas, despacho, factura, cobro y cierre. Definir permisos y volumen de eventos antes de exponer la auditoría técnica al usuario.

Validación sugerida con el PM: pedir a un asesor que encuentre una ODS por placa, identifique las órdenes sin técnico, localice una promesa vencida y explique el próximo paso de una orden completada. Registrar dificultades y ajustar sobre estos casos concretos.

No se cambiaron contratos de API ni reglas de estado para esta mejora. Las propuestas anteriores quedan pendientes de definición; no se presentan como funcionalidad implementada.
