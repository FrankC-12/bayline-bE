"""Holding taller report."""

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select

from app.modules.clients.models import Vehicle
from app.modules.holdings.control_common import ReportContext, aware, table, value
from app.modules.service_orders.models import (
    Bay,
    ServiceOrder,
    ServiceOrderTask,
    ServiceOrderTransfer,
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
    counts = defaultdict(set)
    for o, _vehicle in active:
        if o.bay_id and o.status.value != "completado" and o.vehicle_received_at:
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
    waiting = set(
        (
            await self.db.execute(
                select(ServiceOrderTask.service_order_id).where(
                    ServiceOrderTask.service_order_id.in_(order_ids),
                    ServiceOrderTask.status == "EN_ESPERA_DE_REPUESTOS",
                )
            )
        )
        .scalars()
        .all()
    )
    outstanding = set(
        (
            await self.db.execute(
                select(ServiceOrderTransfer.service_order_id).where(
                    ServiceOrderTransfer.service_order_id.in_(order_ids),
                    ServiceOrderTransfer.status != "COMPLETADO",
                )
            )
        )
        .scalars()
        .all()
    )
    board = []
    for o, v in active:
        phase = (
            "entrega"
            if o.invoiced_at
            else (
                "control_calidad"
                if value(o.status) == "completado"
                else (
                    "repuestos"
                    if o.id in waiting or o.id in outstanding
                    else "taller" if value(o.status) == "en_progreso" else "recepcion"
                )
            )
        )
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
    policies = [record for record in ctx.records if record.kind == "risk_policy"]
    warning_hours = policies[-1].payload["input"]["workshop_warning_hours"] if policies else 48
    ctx.metric("Órdenes activas", len(board))
    ctx.metric("Sin bahía", sum((not o.bay_id for o, v in active)))
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
                "Control de calidad agrupa trabajos completados sin facturar; no certifica "
                "una revisión física adicional."
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
