"""Holding comercial report."""

from sqlalchemy import func, select

from app.modules.concesionario.models import DealershipVehicle, VehicleSale
from app.modules.filiales.models import Filial
from app.modules.holdings.control_common import D, ReportContext, table, value
from app.modules.parts.models import PartSale
from app.modules.service_orders.models import ServiceOrder, ServiceOrderInvoice


async def render(self, ctx: ReportContext):
    from app.modules.administracion.service import AdministracionService

    finance = AdministracionService(self.db)
    rows = []
    for f in ctx.selected:
        p = await finance.get_profitability(f.id, ctx.start, ctx.end)
        sales_count = (
            await self.db.execute(
                select(func.count(VehicleSale.id)).where(
                    VehicleSale.filial_id == f.id,
                    VehicleSale.created_at >= ctx.lower,
                    VehicleSale.created_at < ctx.upper,
                )
            )
        ).scalar() or 0
        part_count = (
            await self.db.execute(
                select(func.count(PartSale.id)).where(
                    PartSale.filial_id == f.id,
                    PartSale.status != "CANCELADO",
                    PartSale.created_at >= ctx.lower,
                    PartSale.created_at < ctx.upper,
                )
            )
        ).scalar() or 0
        invoice_count = (
            await self.db.execute(
                select(func.count(ServiceOrderInvoice.id))
                .join(ServiceOrder, ServiceOrder.id == ServiceOrderInvoice.service_order_id)
                .where(
                    ServiceOrder.filial_id == f.id,
                    ServiceOrderInvoice.issued_at >= ctx.lower,
                    ServiceOrderInvoice.issued_at < ctx.upper,
                )
            )
        ).scalar() or 0
        target_by_day = {
            r.entry_date: D(r.payload["input"]["amount"])
            for r in ctx.records
            if r.kind == "target" and r.filial_id == f.id and (ctx.start <= r.entry_date <= ctx.end)
        }
        goal = sum(target_by_day.values(), D(0))
        count = sales_count + part_count + invoice_count
        rows.append(
            dict(
                filial=f.name,
                filial_id=str(f.id),
                sales=p.net_sales_total,
                target=float(goal) if target_by_day else None,
                achievement=float(D(p.net_sales_total) / goal * 100) if goal else None,
                ticket=p.net_sales_total / count if count else 0,
                margin=p.gross_margin * 100,
                vehicles=sales_count,
                estimated=p.vehicles_with_estimated_cost_count,
            )
        )
    ctx.metric("Ventas netas del período", sum(D(r["sales"]) for r in rows), "money")
    ctx.metric("Vehículos vendidos", sum(r["vehicles"] for r in rows))
    ctx.metric(
        "Meta registrada",
        sum(D(r["target"]) for r in rows if r["target"] is not None),
        "money",
        note="Solo días con meta registrada",
    )
    ctx.report.tables.append(
        table(
            "Resultados por filial",
            [
                ("filial", "Filial"),
                ("sales", "Ventas netas", "money"),
                ("target", "Meta", "money"),
                ("achievement", "Cumplimiento %", "number"),
                ("ticket", "Ticket neto", "money"),
                ("margin", "Margen bruto %", "number"),
                ("vehicles", "Vehículos", "number"),
                ("estimated", "Costos estimados", "number"),
            ],
            rows,
        )
    )
    units = (
        await self.db.execute(
            select(Filial.name, DealershipVehicle.condition, func.count(VehicleSale.id))
            .join(VehicleSale, VehicleSale.filial_id == Filial.id)
            .join(DealershipVehicle, DealershipVehicle.id == VehicleSale.vehicle_id)
            .where(
                VehicleSale.filial_id.in_(ctx.ids),
                VehicleSale.created_at >= ctx.lower,
                VehicleSale.created_at < ctx.upper,
            )
            .group_by(Filial.name, DealershipVehicle.condition)
        )
    ).all()
    ctx.report.tables.append(
        table(
            "Unidades por condición",
            [("filial", "Filial"), ("condition", "Condición"), ("count", "Unidades", "number")],
            [dict(filial=f, condition=value(c), count=n) for f, c, n in units],
        )
    )
    latest = {}
    for r in ctx.records:
        if r.kind == "opportunity" and r.entry_date <= ctx.end:
            latest[r.payload["evidence"].get("root_id") or str(r.id)] = r
    ctx.report.tables.append(
        table(
            "Embudo comercial",
            [
                ("filial", "Filial"),
                ("client", "Cliente"),
                ("description", "Oportunidad"),
                ("stage", "Etapa", "status"),
                ("amount", "Valor USD", "money"),
            ],
            [
                dict(
                    id=str(r.id),
                    filial=ctx.names[r.filial_id],
                    **r.payload["input"],
                )
                for r in latest.values()
            ],
            "Oportunidades registradas en el holding, con historial de cambios de etapa.",
        )
    )
