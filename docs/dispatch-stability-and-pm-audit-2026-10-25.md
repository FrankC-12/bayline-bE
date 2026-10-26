# Despachos y revisión de los seis tickets del PM

Revisión del 25/10/2026 sobre el código disponible. No se consultaron el despliegue, los saldos ni las pantallas de producción; “implementado” no equivale a aceptación del PM.

## Despachos

Se eliminaron dos comportamientos que hacían perder contexto: iniciar una preparación abría el modal automáticamente y cada cambio de estado cambiaba el filtro activo. También se eliminó el desplazamiento forzado al cerrar el detalle.

La bandeja inicia en Activos (pendientes, en preparación y por retirar). Los filtros específicos y Completados siguen disponibles. Al avanzar una solicitud, se conserva el filtro y se mantiene visible la fila actualizada, aunque su nuevo estado ya no coincida con ese filtro. Se resalta e identifica con el estado real; el aviso explica por qué sigue visible. “Ver solicitud” abre el detalle solo cuando el almacenista lo elige. “Entendido” o elegir otro filtro quita ese seguimiento.

No se adelanta visualmente un estado antes de confirmar la respuesta del backend: se conservan POST y posterior lectura autoritativa. Las respuestas de un almacén/filial anterior no muestran avisos en el nuevo contexto. Si cambia la cantidad de una línea mientras se revisa el despacho, se reinicia su checklist para no confirmar cantidades distintas de las comprobadas.

## Tickets del PM

| Ticket | Estado de revisión | Evidencia y condición para cerrarlo |
| --- | --- | --- |
| BAY-19 — Cuentas/precios | Corrección implementada; falta contrastar producción | Cotización FIFO compartida entre temparios y ODS, margen una sola vez y redondeo a centavos. La prueba explícita de $12,70 coincide entre catálogo y ODS con los mismos ajustes. El usuario relacionó este ticket con ese cálculo. No se verificó una conciliación de saldos de Motrix ni un registro real de producción. |
| BAY-14 — Costo no editable | Implementado en frontend y backend | El editor muestra costo como texto. `_catalog_part_costs` toma compras o conserva el estimado previo; un PATCH con costo arbitrario no lo impone. Prueba de modificación de costo rechazada por normalización. La carga inicial de un estimado sigue siendo una operación distinta de editar el costo en el tempario. |
| BAY-09 — ODS En progreso | Implementado y probado | `start_task_timer` marca la tarea en progreso y sincroniza la ODS en la misma transacción. Cambiar directamente el estado de la tarea también sincroniza. La prueba comprueba ambos estados tras iniciar. |
| BAY-08 — Renderizado de repuestos | Implementación actualizada; por validar visualmente con el PM | Tabla de ODT con nombre/código, cantidad, PVP y total, límites de anchura y desplazamiento horizontal dentro de la tabla. Se conserva la separación entre ODT y sus subtotales. No se reprodujo en navegador el defecto de producción que motivó “Retrabajo”; no se marca aceptado solo por existir estos cambios. |
| BAY-06 — Imagen del vehículo | Parcial | Se mejoró el esquema SVG del mapa de daños conservando coordenadas históricas. No se sustituyó por una imagen definitiva aprobada por el PM. Este punto necesita esa referencia/aceptación antes de considerarse cerrado. |
| BAY-05 — Fluidos e interior | Implementado y probado de extremo a extremo | El contrato acepta `fluids` e `interior`, los persiste y la consulta de la ODS muestra campos, fotos y valores cero. La prueba HTTP con PostgreSQL comprobó los datos de ambas inspecciones y fotografías. Mobile tiene que enviar esos campos y las migraciones correspondientes deben estar aplicadas; el portal no puede recuperar información que no llegó al servidor. |

## Verificación de esta revisión

- 16 pruebas backend focalizadas: precios, bloqueo de costo, estados de tarea/ODS y FIFO.
- 1 prueba HTTP/PostgreSQL focalizada: fluidos, interior y fotos consultados desde la ODS; 86 pruebas de integración no relacionadas fueron deseleccionadas.
- TypeScript sin errores; lint del portal sin advertencias; 71 pruebas frontend aprobadas.
- Diferencias de ambos repositorios sin errores de espacios.

No se realizó una sesión de navegador interactiva. Esta mejora de Despachos no introduce cambios de API, migraciones ni variables de entorno.
