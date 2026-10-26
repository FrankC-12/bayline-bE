# Seguimiento de auditoría funcional — 26 de octubre de 2026

Fuente: «Auditoría funcional Bayline.md», servidor de pruebas, Taller del Este.
Este documento describe cambios locales y su validación; no acredita despliegue ni saneamiento de producción.

## Modelo confirmado por el responsable del producto

El holding es el **importador/distribuidor de una red de concesionarios**.
La afiliación no implica que sea propietario del concesionario. Por ejemplo, un
concesionario independiente puede estar afiliado al distribuidor Ford de Venezuela.
`holding_id` expresa pertenencia a esa red y delimita el acceso a sus datos.

Los reportes agregan operaciones de la red. Sus totales no son por sí mismos estados
financieros legales de empresas bajo control societario. Se explicita en los reportes.
Los arqueos los registra el concesionario; el distribuidor los consulta.

Esta decisión orienta futuras ampliaciones: pedidos B2B al distribuidor, trazabilidad
por VIN, facturación distribuidor/concesionario y revisión de garantías. No convierte
por sí sola los traslados de inventario existentes en compraventas entre empresas.

## Correcciones de los hallazgos

| ID | Cambio implementado |
| --- | --- |
| H1 | La alerta detecta ventas inferiores al costo aunque no tengan la marca de excepción. Muestra costo, resultado y autorización registrada. Las ventas nuevas conservan el costo al vender; las históricas sin snapshot usan el costo disponible, sin inventar autorizaciones. |
| H2 | Porcentajes con dos decimales y símbolo %. Los valores de rentabilidad siguen siendo fracciones internamente y se convierten una sola vez. Una pérdida puede superar el 100 % de las ventas: no se recorta ni se confunde con un error matemático. |
| H3 | Resumen y Comercial usan el mismo reporte. Mes actual por defecto; período y concesionario se conservan al navegar, separados por red. Comercial muestra el costo directo y distingue el **conteo** de vehículos con costo estimado. Contabilidad usa la misma rentabilidad. |
| H4 | Recursos y campos legibles; referencias ODS/tareas/despachos se resuelven por lotes. Los eventos nuevos guardan también una etiqueta del recurso. Una ODS enviada se cancela con motivo; solo se eliminan borradores protegidos contra movimientos de inventario. |
| H5 | Holding y calendario comparten fases. «Pendiente de facturar» sustituye la clasificación ficticia de control de calidad. Ocupación requiere bahía activa y recepción o inspección preliminar completada; las asignaciones sin recepción se muestran aparte. Se agrega carga por técnico. |
| H6 | Holding no puede registrar arqueos de una filial. Finanzas del concesionario dispone de «Arqueo de caja», con moneda original, diferencia y responsable. El arqueo deja evidencia y no ajusta silenciosamente el saldo. |
| H7 | Perfil de filial con razón social, RIF, dirección fiscal, tipo, marcas representadas, contribuyente especial y serie. Alta y edición disponibles. Registros anteriores pueden completarse sin inventar datos fiscales. Esto no sustituye un módulo fiscal autorizado. |
| S1 | Inicio y finalización comprueban repuestos retirados e inspecciones preliminar/minuciosa completadas. No se completa una tarea nunca iniciada. El cronómetro acumula segundos sin perder segmentos subsegundo por redondeo. Completado forzado queda reservado a Administración y conserva trazabilidad de pendientes. |
| S2 | PVP por código, separado del costo FIFO. Catálogo, tempario, ODS, venta de mostrador y cotización de upsell usan el PVP configurado. La asignación/consumo de lotes actualiza costo, no el precio de la ODT. Cantidades conservan el precio unitario cotizado. Los cambios explícitos de política comercial siguen siendo operaciones deliberadas, no efectos del despacho. |
| S3 | Validación central de kilometraje en inspecciones, edición del vehículo, entrega y ambos caminos de reclamos. Retrocesos requieren corrección justificada; los endpoints de inspección exigen Administración para corregir. Valores extraordinarios requieren justificación. Se limita el año futuro al siguiente año modelo: un modelo 2027 en 2026 puede ser legítimo. |
| S4 | La venta usa el precio de catálogo calculado por el servidor. Alta manual de unidades y cambios de precio/costo requieren Administración; cambios exigen motivo y dejan evento. No se permite cambiar el precio y vender en la misma solicitud. El bloqueo/autorización de venta bajo costo se conserva. |
| S5 | VIN de 17 caracteres alfanuméricos, excluyendo I/O/Q. Placa normalizada y validada; duplicados de placa/VIN entre clientes de un concesionario se rechazan, serializando su escritura. Un registro provisional puede no tener VIN; enviarlo al taller lo exige. |
| S6 | Se rechaza una segunda visita activa para el vehículo/VIN, indicando la ODS existente. Aplica tanto a creación móvil como a envío del borrador web. Los borradores no representan visitas iniciadas y pueden prepararse por separado. |
| S7 | CxC y rentabilidad de mostrador reconocen ventas entregadas, no solicitudes en preparación. Nueva fecha de entrega fija antigüedad y período comercial/contable. Cobros anticipados siguen como movimientos de caja, sin crear una deuda ficticia. |
| S8 | Egresos de compras a proveedores deben identificar la CxP que cancelan; se conservan controles de proveedor, moneda, importe y pago duplicado. Cambiar un estado genérico no puede simular conciliación o pago. Compras históricas sin factura se identifican como pendientes de conciliar en la lista. |
| S9 | La agenda comprueba solapamientos de bahía **y técnico**, incluyendo cambios y aumento de duración al agregar tareas. El holding muestra órdenes y horas pendientes por técnico. Las visitas sin agenda/recepción se muestran fuera de la grilla, sin inventar citas a las 08:00. Planificación por turnos y capacidad diaria configurable sigue siendo una ampliación P1. |
| S10 | Facturas con desglose congelado separan repuestos y mano de obra; IVA/IGTF no se suman a la venta neta de taller. Costo de repuestos procede de despachos con salida física, no reservas. Facturas históricas sin desglose conservan su agrupación: no se inventa una distribución retrospectiva. |
| S11 | Borradores nuevos usan referencia provisional BOR y no consumen la secuencia oficial. El número ODS se asigna al enviar al taller. No se renumeran órdenes ni borradores históricos que ya tienen número positivo. |
| S12 | Se diferencia estado del trabajo de su fase: completado sin factura = pendiente de facturar; con factura pendiente = por cobrar; pagada = pendiente de entrega; entrega = cerrada. Holding y calendario reutilizan las mismas fases; el estado contractual de la API no se renombra para romper el móvil. |
| S13 | El importe aprobado de un upsell se calcula solo con sus nuevas tareas y líneas, se guarda y se muestra en el histórico. No se consulta el total de la ODS como importe vendido ni se recalcula una aprobación con el catálogo actual. |

## Migración y compatibilidad

Nueva revisión Alembic: `d1a804be6202`, posterior a `c0e671af4201`.

- Agrega PVP de lista, costo guardado de venta de vehículo, importes aprobados y precios
  cotizados de upsells, etiqueta legible de auditoría, entrega de venta de repuestos y
  datos fiscales de filiales.
- Inicializa el PVP de los repuestos existentes con la referencia de su último costo
  registrado × 1,30. Es una **base de transición a revisar**, no una lista oficial de
  precios suministrada por el distribuidor.
- Un código sin historial de costo queda sin PVP configurado. El formulario solicita
  PVP y el tempario advierte que debe configurarse antes de aprobar el presupuesto.
  Se mantiene una estimación compatible para consumidores antiguos; no debe tratarse
  como precio oficial de lista.
- Conserva precios de facturas/ODT históricas, números positivos y pagos registrados.
- Ventas antiguas sin fecha de entrega usan su fecha original como referencia histórica;
  no se rellena una fecha de entrega ficticia.
- Cambia la semántica de CxC de mostrador a ventas entregadas. Los anticipos no se
  convierten automáticamente en pasivos contables; requieren el módulo contable fiscal.

Desplegar imágenes compatibles de backend/frontend y ejecutar:

```sh
docker exec app-backend-1 alembic upgrade head
```

No basta ejecutar una migración con una imagen antigua que aún no contiene esta revisión.
No se ejecutó este comando contra EC2 durante este trabajo.

## Contrato de flujos para web y móvil

- El envío de borrador puede responder `vehicle_has_active_order`, `vehicle_vin_required`
  o `bay_schedule_conflict`; se debe mostrar el mensaje y continuar el registro existente.
- Ejecución exige inspecciones completas y repuestos retirados. Errores:
  `work_inspections_required`, `task_parts_not_received`, `task_not_started`.
- Un técnico no puede usar el completado forzado como atajo del flujo normal.
- Compras se concilian y pagan por sus operaciones financieras; el endpoint genérico
  de estado devuelve `purchase_financial_workflow_required` si se intenta sustituirlas.
- `POST /cash-counts` recibe `RegisterCreate` con `record.kind=cash_count`; solo permite
  un usuario de la propia filial, con permiso de Finanzas. Conserva idempotencia.
- Eliminación de ODS enviada: `order_delete_requires_draft`. Usar cancelación con motivo.
- Campos nuevos de lectura son aditivos; los estados persistidos existentes se conservan.
  Las fases derivadas distinguen `por_facturar`, `por_cobrar` y `por_entregar`.

## Conciliación de datos y alcance pendiente

No se reconstruyen desde texto libre los pagos de SC-1001, ni se cambian automáticamente
kilometrajes, autorizaciones, garantías o precios históricos. Antes del piloto:

1. Revisar la lista oficial de PVP y completar códigos sin precio.
2. Conciliar egresos antiguos con facturas/CxP usando sus comprobantes; no volver a pagar
   una compra solo porque una deuda histórica siga abierta.
3. Corregir identidades duplicadas e historiales de kilometraje con evidencia y autorización.
4. Revisar ODS históricas completadas sin inspección/repuestos/tiempo real.
5. Completar perfiles fiscales y comprobar los resultados con los datos del servidor de pruebas.

El CRM de vehículos, reservas/prospectos completos, contratos, PDI, retomas, libros de
IVA, notas de crédito/débito, capacidades por turnos, metas mensuales por departamento,
pedidos/facturación B2B y garantías liquidadas ODS por ODS son **ampliaciones P1/P2** de
la auditoría. No se presentan como implementadas por estas correcciones de errores.
HTTPS, dominio y rotación de credenciales son trabajo de infraestructura pendiente;
no se modificó la configuración de EC2 ni sus claves.

## Validación

Resultados locales finales:

- Backend: **852 pruebas aprobadas**, incluidas 18 regresiones de la auditoría.
- PostgreSQL: **90 pruebas de integración aprobadas**; migración subida, revertida y subida de nuevo en un contenedor desechable.
- Frontend: **81 pruebas aprobadas**, TypeScript sin errores y compilación Next exitosa.
- Revisión de nombres/importaciones de Python (`ruff`, categoría F) y `git diff --check`: sin errores.
- Persiste un aviso previo de dependencias de un hook en `BillingModal`; no impide la compilación.

Las pruebas no sustituyen la aceptación funcional del PM en el servidor desplegado.
No se alteró la base de datos de la aplicación ni se desplegó en producción.
