# Refactor de navegación y vistas Motrix

Cambios en frontend y backend. Requiere desplegar ambos y ejecutar `alembic upgrade head`.
La migración nueva es `e8bf71cd9659` (después de las migraciones de leads y oportunidades).

## Disponible

- Sidebar global por permisos, navegación móvil, acceso del holding y cierre de sesión.
- Inicio con agregados reales por filial: ODS, borradores, oportunidades, inventario y clientes.
- Búsqueda global limitada por permisos y filial (clientes, unidades y número de ODS).
- Catálogo compacto, filtros por condición/estado, exportación CSV y ficha con pestañas.
- Clientes adaptado al espacio del nuevo sidebar.
- Detalle de oportunidades como página, con etapas, resumen y actividad.
- Ajustes → Reglas de venta: vigencia de cotización (15 días), reserva (7 días),
  abono mínimo (10 %), descuento sin aprobación (3 %) y retención por desistimiento (10 %).
- El backend valida cotización, descuento máximo, vencimiento, abono y plazo de reserva.
- Al vender desde la oportunidad se envían su identificador y versión; el backend conserva
  el importe cotizado, valida cliente/unidad/moneda/tipo de venta y calcula el IGTF del pago.
  Las ventas directas del catálogo conservan su flujo existente.

## Alcance pendiente respecto a las referencias

El inicio no inventa márgenes, metas ni saldos consolidados: los indicadores financieros
avanzados y objetivos por sucursal de la referencia todavía no están incorporados aquí.
No se agregó un selector de moneda decorativo: formularios y documentos mantienen su moneda real.

La continuación del flujo comercial ya incorpora recaudos privados, decisión externa de crédito,
contrato y seguro, abono/retención/devolución enlazados a Finanzas, PDI automática y checklist de entrega.
Ver [vehicle-sales-workflow.md](vehicle-sales-workflow.md) para operación, compatibilidad e integración.
La nueva migración de esa continuación es `f9c082de076a`.
