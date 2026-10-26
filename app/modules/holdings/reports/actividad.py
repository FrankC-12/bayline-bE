"""Holding actividad report."""

from sqlalchemy import func, or_, select

from app.modules.concesionario.models import DealershipVehicle, VehicleSale
from app.modules.holdings.audit_labels import field_labels, order_references
from app.modules.holdings.control_common import ReportContext, table
from app.modules.holdings.control_models import HoldingAuditEvent
from app.modules.users.models import User


async def render(self, ctx: ReportContext):
    events = (
        await self.db.execute(
            select(HoldingAuditEvent, User.full_name)
            .outerjoin(User, User.id == HoldingAuditEvent.user_id)
            .where(
                HoldingAuditEvent.holding_id == ctx.holding_id,
                *([HoldingAuditEvent.filial_id == ctx.filial_id] if ctx.filial_id else []),
                HoldingAuditEvent.created_at >= ctx.lower,
                HoldingAuditEvent.created_at < ctx.upper,
            )
            .order_by(HoldingAuditEvent.created_at.desc(), HoldingAuditEvent.id)
            .limit(200)
        )
    ).all()
    references = await order_references(self.db, events, ctx.ids)
    ctx.metric("Eventos visibles", len(events), note="Últimos 200 del período")
    ctx.report.tables.append(
        table(
            "Actividad y auditoría",
            [
                ("date", "Fecha", "datetime"),
                ("filial", "Filial"),
                ("user", "Usuario"),
                ("action", "Acción", "status"),
                ("entity", "Recurso"),
                ("resource", "Referencia"),
                ("fields", "Campos modificados"),
            ],
            [
                dict(
                    id=str(e.id),
                    date=e.created_at,
                    filial=ctx.names.get(e.filial_id, "Holding"),
                    user=u or "Sistema",
                    action=e.action,
                    entity={
                        "accounts": "Cuenta financiera",
                        "income_entries": "Ingreso",
                        "expense_entries": "Egreso",
                        "vehicle_sales": "Venta de vehículo",
                        "part_sales": "Venta de repuestos",
                        "service_orders": "Orden de servicio",
                        "service_order_invoices": "Factura de taller",
                        "part_lots": "Lote de repuestos",
                        "purchase_requests": "Compra",
                        "holding_registers": "Operación del holding",
                        "users": "Usuario",
                        "warehouse_transfers": "Transferencia de almacén",
                        "service_order_transfers": "Despacho de taller",
                        "service_order_transfer_lines": "Repuesto de orden de servicio",
                        "service_order_tasks": "Tarea de orden de servicio",
                    }.get(e.entity, "Registro del sistema"),
                    resource=e.resource_label
                    or references.get(
                        e.resource_id, f"Registro {e.resource_id[:8]}" if e.resource_id else "—"
                    ),
                    fields=field_labels(e.changed_fields),
                )
                for e, u in events
            ],
            (
                "Auditoría persistente a partir de esta actualización; se publica después "
                "de confirmar la transacción."
            ),
        )
    )
    risk_sales = (
        await self.db.execute(
            select(
                VehicleSale, func.coalesce(VehicleSale.cost_snapshot, DealershipVehicle.cost_price)
            )
            .join(DealershipVehicle, DealershipVehicle.id == VehicleSale.vehicle_id)
            .where(
                VehicleSale.filial_id.in_(ctx.ids),
                or_(
                    VehicleSale.below_cost_override.is_(True),
                    VehicleSale.final_price
                    < func.coalesce(VehicleSale.cost_snapshot, DealershipVehicle.cost_price),
                ),
                VehicleSale.created_at >= ctx.lower,
                VehicleSale.created_at < ctx.upper,
            )
            .order_by(VehicleSale.created_at.desc())
            .limit(100)
        )
    ).all()
    ctx.report.tables.append(
        table(
            "Alertas: ventas bajo costo",
            [
                ("date", "Fecha", "datetime"),
                ("filial", "Filial"),
                ("code", "Venta"),
                ("description", "Justificación"),
                ("authorization", "Autorización"),
                ("cost", "Costo registrado", "money"),
                ("loss", "Resultado", "money"),
                ("amount", "Total", "money"),
            ],
            [
                dict(
                    date=s.created_at,
                    filial=ctx.names[s.filial_id],
                    code=s.code,
                    description=s.below_cost_override_note or "Sin nota",
                    authorization=(
                        "Autorización registrada"
                        if s.below_cost_override and s.authorized_by_user_id and s.authorized_at
                        else "Sin autorización registrada: revisar"
                    ),
                    cost=float(cost) if cost is not None else None,
                    loss=float(s.final_price - cost) if cost is not None else None,
                    amount=float(s.final_price),
                )
                for s, cost in risk_sales
            ],
            (
                "Incluye excepciones autorizadas y ventas por debajo del costo registrado, "
                "aunque no tengan autorización histórica. Se usa el costo guardado al vender o, para ventas antiguas, el costo disponible; "
                "la ausencia de registro de autorización requiere revisión."
            ),
        )
    )
    from datetime import UTC, datetime, timedelta

    from app.modules.holdings.control_common import D, aware
    from app.modules.parts.models import PartSale
    from app.modules.parts.pricing import PARTS_MULTIPLIERS
    from app.modules.service_orders.models import ServiceOrder

    policies = [r for r in ctx.records if r.kind == "risk_policy"]
    policy = policies[-1].payload["input"] if policies else {}
    threshold = policy.get("max_discount_percentage")
    hours = policy.get("workshop_warning_hours", 48)
    ctx.metric("Alerta de taller (horas)", hours)
    ctx.metric(
        "Límite de descuento %",
        float(threshold) if threshold is not None else None,
        "percent",
        note="Configurable por el holding; se compara con PVP normal",
    )
    stale = (
        (
            await self.db.execute(
                select(ServiceOrder)
                .where(
                    ServiceOrder.filial_id.in_(ctx.ids),
                    ServiceOrder.is_draft.is_(False),
                    ServiceOrder.status.in_(["PENDIENTE", "EN_PROGRESO", "COMPLETADO"]),
                    ServiceOrder.created_at < datetime.now(UTC) - timedelta(hours=hours),
                )
                .order_by(ServiceOrder.created_at)
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    ctx.report.tables.append(
        table(
            "Alertas: permanencia en taller",
            [
                ("code", "ODS"),
                ("filial", "Filial"),
                ("status", "Estado", "status"),
                ("hours", "Horas transcurridas", "number"),
            ],
            [
                dict(
                    code=f"ODS-{o.sequence_number}",
                    filial=ctx.names[o.filial_id],
                    status=o.status.value,
                    hours=round(
                        (datetime.now(UTC) - aware(o.created_at)).total_seconds() / 3600, 1
                    ),
                )
                for o in stale
            ],
            f"Órdenes activas con más de {hours} horas. "
            "Umbral de seguimiento; no es una promesa de entrega.",
        )
    )
    discounts = []
    if threshold is not None:
        for model in (PartSale, ServiceOrder):
            candidates = (
                (
                    await self.db.execute(
                        select(model)
                        .where(
                            model.filial_id.in_(ctx.ids),
                            model.created_at >= ctx.lower,
                            model.created_at < ctx.upper,
                        )
                        .order_by(model.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            for candidate in candidates:
                if candidate.status.value in {"cancelado"} or getattr(candidate, "is_draft", False):
                    continue
                multiplier = PARTS_MULTIPLIERS.get(candidate.discount_label)
                if multiplier is None:
                    continue
                discount = (D(1) - D(multiplier) / D("1.30")) * 100
                if discount > D(threshold):
                    discounts.append(
                        dict(
                            filial=ctx.names[candidate.filial_id],
                            document=(
                                f"{'VR' if model is PartSale else 'ODS'}-"
                                f"{candidate.sequence_number}"
                            ),
                            policy=candidate.discount_label,
                            discount=float(discount.quantize(D("0.01"))),
                            limit=float(threshold),
                        )
                    )
    ctx.report.tables.append(
        table(
            "Alertas: descuentos sobre el límite del holding",
            [
                ("filial", "Filial"),
                ("document", "Documento"),
                ("policy", "Política aplicada"),
                ("discount", "Descuento sobre PVP %", "number"),
                ("limit", "Límite configurado %", "number"),
            ],
            discounts,
            (
                "Compara la política de repuestos con el precio normal (costo + 30 %). "
                "Es una alerta de control; no modifica precios ni revoca autorizaciones previas."
                if threshold is not None
                else "Configura un límite de descuento para activar este monitor."
            ),
        )
    )
