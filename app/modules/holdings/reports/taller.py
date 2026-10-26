"""Holding taller report."""

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.modules.clients.models import Vehicle
from app.modules.holdings.control_common import ReportContext, aware, table, value
from app.modules.service_orders.models import (
    Bay,
    ServiceOrder,
    ServiceOrderTask,
)


async def render(self, ctx: ReportContext):
    active = (
        await self.db.execute(
            select(ServiceOrder, Vehicle)
            .outerjoin(Vehicle, Vehicle.id == ServiceOrder.vehicle_id)
            .where(
                ServiceOrder.filial_id.in_(ctx.ids),
                ServiceOrder.is_draft.is_(False),
                ServiceOrder.status.in_(["PENDIENTE", "EN_PROGRESO", "COMPLETADO"]),
            )
            .order_by(ServiceOrder.created_at)
        )
    ).all()
    bays = (
        (
            await self.db.execute(
                select(Bay).where(Bay.filial_id.in_(ctx.ids), Bay.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    from app.modules.inspections.enums import InspectionStatus
    from app.modules.inspections.models import PreliminaryInspection

    received = set(
        (
            await self.db.execute(
                select(PreliminaryInspection.service_order_id).where(
                    PreliminaryInspection.service_order_id.in_([order.id for order, _ in active]),
                    PreliminaryInspection.status == InspectionStatus.COMPLETADA,
                )
            )
        )
        .scalars()
        .all()
    )
    active_bays = {bay.id for bay in bays}
    counts = defaultdict(set)
    for o, _vehicle in active:
        if (
            o.bay_id in active_bays
            and o.status.value != "completado"
            and (o.vehicle_received_at or o.id in received)
        ):
            counts[o.filial_id].add(o.bay_id)
    ctx.report.tables.append(
        table(
            "Ocupación actual de bahías",
            [
                ("filial", "Filial"),
                ("occupied", "Ocupadas", "number"),
                ("total", "Disponibles", "number"),
                ("occupancy", "Ocupación %", "number"),
            ],
            [
                dict(
                    filial=f.name,
                    occupied=len(counts[f.id]),
                    total=sum(b.filial_id == f.id for b in bays),
                    occupancy=(
                        round(len(counts[f.id]) / sum(b.filial_id == f.id for b in bays) * 100, 2)
                        if any(b.filial_id == f.id for b in bays)
                        else None
                    ),
                )
                for f in ctx.selected
            ],
        )
    )
    order_ids = [o.id for o, v in active]
    from app.modules.service_orders.service import ServiceOrderService

    await ServiceOrderService(self.db).attach_workflow([order for order, _ in active])
    board = []
    for o, v in active:
        phase = o.workshop_stage
        hours = (datetime.now(UTC) - aware(o.created_at)).total_seconds() / 3600
        board.append(
            dict(
                id=str(o.id),
                filial=ctx.names[o.filial_id],
                code=f"ODS-{o.sequence_number}",
                vehicle=f"{v.brand} {v.model}" if v else "Sin vehículo",
                plate=v.plate if v else "",
                phase=phase,
                hours=round(hours, 1),
                status=value(o.status),
            )
        )
    from app.modules.users.models import User

    technicians = {
        user.id: user.full_name
        for user in (
            await self.db.execute(
                select(User).where(
                    User.id.in_(
                        {
                            order.technician_user_id
                            for order, _ in active
                            if order.technician_user_id
                        }
                    )
                )
            )
        )
        .scalars()
        .all()
    }
    workloads = defaultdict(lambda: {"orders": 0, "hours": 0.0})
    for order, _ in active:
        if order.technician_user_id:
            workloads[order.technician_user_id]["orders"] += 1
    for technician_id, hours in (
        await self.db.execute(
            select(ServiceOrder.technician_user_id, func.sum(ServiceOrderTask.hours_snapshot))
            .join(ServiceOrderTask, ServiceOrderTask.service_order_id == ServiceOrder.id)
            .where(
                ServiceOrder.id.in_(order_ids),
                ServiceOrderTask.status.notin_(["COMPLETADA", "CANCELADA"]),
            )
            .group_by(ServiceOrder.technician_user_id)
        )
    ).all():
        if technician_id:
            workloads[technician_id]["hours"] = float(hours or 0)
    ctx.report.tables.append(
        table(
            "Carga por técnico",
            [
                ("technician", "Técnico"),
                ("orders", "Órdenes activas", "number"),
                ("hours", "Horas pendientes del tempario", "number"),
            ],
            [
                dict(technician=technicians.get(key, "Sin registrar"), **load)
                for key, load in workloads.items()
            ],
            "La agenda impide solapamientos de técnico y bahía. La cola de órdenes sin horario no se interpreta como trabajo simultáneo.",
        )
    )
    policies = [record for record in ctx.records if record.kind == "risk_policy"]
    warning_hours = policies[-1].payload["input"]["workshop_warning_hours"] if policies else 48
    ctx.metric("Órdenes activas", len(board))
    ctx.metric("Sin bahía", sum((not o.bay_id for o, v in active)))
    ctx.metric(
        "Asignadas sin recepción confirmada",
        sum(
            bool(o.bay_id) and not o.vehicle_received_at and o.id not in received for o, _ in active
        ),
        note="Asignación y ocupación física son indicadores diferentes",
    )
    ctx.metric(
        f"Con más de {warning_hours} h",
        sum(x["hours"] > warning_hours for x in board),
        note="Umbral operativo; no es fecha prometida",
    )
    ctx.report.tables.append(
        table(
            "Órdenes por fase",
            [
                ("code", "ODS"),
                ("filial", "Filial"),
                ("vehicle", "Vehículo"),
                ("plate", "Placa"),
                ("phase", "Fase", "status"),
                ("hours", "Horas de ciclo", "number"),
            ],
            board,
            (
                "Pendiente de facturar agrupa trabajos completados sin factura. "
                "La ocupación requiere recepción registrada o inspección preliminar completada y una bahía activa."
            ),
        )
    )
    closed = (
        (
            await self.db.execute(
                select(ServiceOrder).where(
                    ServiceOrder.filial_id.in_(ctx.ids),
                    ServiceOrder.closed_at >= ctx.lower,
                    ServiceOrder.closed_at < ctx.upper,
                )
            )
        )
        .scalars()
        .all()
    )
    ctx.metric("Entregas del período", len(closed))
    ctx.metric(
        "Ciclo promedio (horas)",
        (
            round(
                sum(
                    (aware(o.closed_at) - aware(o.created_at)).total_seconds() / 3600
                    for o in closed
                )
                / len(closed),
                1,
            )
            if closed
            else None
        ),
    )
