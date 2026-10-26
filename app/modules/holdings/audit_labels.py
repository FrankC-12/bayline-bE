"""Manager-facing audit labels; raw identifiers remain in the audit ledger."""

import uuid

from sqlalchemy import select

from app.modules.service_orders.models import (
    ServiceOrder,
    ServiceOrderTask,
    ServiceOrderTransfer,
    ServiceOrderTransferLine,
)

FIELD_LABELS = {
    "id": "Identificador",
    "filial_id": "Concesionario",
    "holding_id": "Distribuidor",
    "created_at": "Fecha de creación",
    "updated_at": "Última modificación",
    "status": "Estado",
    "is_draft": "Borrador",
    "draft_revision": "Versión del borrador",
    "draft_upsells": "Recomendaciones del borrador",
    "sequence_number": "Número",
    "vehicle_id": "Vehículo",
    "client_id": "Cliente",
    "name": "Nombre",
    "full_name": "Nombre completo",
    "notes": "Notas",
    "customer_reason": "Motivo del cliente",
    "technician_user_id": "Técnico",
    "advisor_user_id": "Asesor",
    "bay_id": "Bahía",
    "scheduled_at": "Visita agendada",
    "closed_at": "Cierre",
    "cancel_reason": "Motivo de cancelación",
    "cancelled_at": "Cancelación",
    "cancelled_by_user_id": "Responsable de cancelación",
    "reopened_at": "Reapertura",
    "reopened_by_user_id": "Responsable de reapertura",
    "invoiced_at": "Facturación",
    "total_amount": "Total",
    "quantity": "Cantidad",
    "part_id": "Repuesto",
    "unit_price": "PVP",
    "line_total": "Total de línea",
    "cost_total": "Costo",
    "unit_cost": "Costo unitario",
    "payer": "Responsable del pago",
    "stock_deducted": "Salida de inventario",
    "picked_up_at": "Retiro de repuestos",
    "timer_started_at": "Inicio del cronómetro",
    "timer_accumulated_seconds": "Tiempo trabajado",
    "completed_at": "Finalización",
    "amount": "Monto",
    "currency": "Moneda",
    "price_cash": "Precio de contado",
    "price_financed": "Precio financiado",
    "final_price": "Importe de venta",
    "cost_price": "Costo de adquisición",
    "pricing_snapshot": "Presupuesto congelado",
    "settings_snapshot": "Condiciones de la orden",
    "creation_source": "Origen",
    "order_type_id": "Tipo de orden",
}


def field_labels(fields):
    labels = [FIELD_LABELS[field] for field in fields if field in FIELD_LABELS]
    unknown = sum(field not in FIELD_LABELS for field in fields)
    if unknown:
        labels.append(f"Otros datos del registro ({unknown})")
    return ", ".join(labels) or "Sin cambios de campos"


async def order_references(db, events, filial_ids):
    identifiers = set()
    for event, _ in events:
        try:
            identifiers.add(uuid.UUID(event.resource_id))
        except (TypeError, ValueError, AttributeError):
            pass
    if not identifiers:
        return {}
    references = {}
    queries = [
        (
            select(ServiceOrder.id, ServiceOrder.sequence_number).where(
                ServiceOrder.id.in_(identifiers), ServiceOrder.filial_id.in_(filial_ids)
            ),
            "ODS",
        ),
        (
            select(ServiceOrderTask.id, ServiceOrder.sequence_number)
            .join(ServiceOrder, ServiceOrder.id == ServiceOrderTask.service_order_id)
            .where(ServiceOrderTask.id.in_(identifiers), ServiceOrder.filial_id.in_(filial_ids)),
            "Tarea de ODS",
        ),
        (
            select(ServiceOrderTransfer.id, ServiceOrder.sequence_number)
            .join(ServiceOrder, ServiceOrder.id == ServiceOrderTransfer.service_order_id)
            .where(
                ServiceOrderTransfer.id.in_(identifiers), ServiceOrder.filial_id.in_(filial_ids)
            ),
            "Despacho de ODS",
        ),
        (
            select(ServiceOrderTransferLine.id, ServiceOrder.sequence_number)
            .join(
                ServiceOrderTransfer,
                ServiceOrderTransfer.id == ServiceOrderTransferLine.transfer_id,
            )
            .join(ServiceOrder, ServiceOrder.id == ServiceOrderTransfer.service_order_id)
            .where(
                ServiceOrderTransferLine.id.in_(identifiers), ServiceOrder.filial_id.in_(filial_ids)
            ),
            "Repuesto de ODS",
        ),
    ]
    for query, label in queries:
        for identifier, number in (await db.execute(query)).all():
            references[str(identifier)] = f"{label}-{number}"
    return references
