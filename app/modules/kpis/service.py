import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.kpis.schemas import KpiReport, KpiRow
from app.modules.service_orders.models import ServiceOrder, ServiceOrderTransfer


def _bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    end = datetime.combine(date_to, time.max, tzinfo=timezone.utc)
    return start, end


def _build_report(rows: list[tuple[uuid.UUID, float]]) -> KpiReport:
    """`rows` is a list of (user_id, elapsed_hours) — one entry per completed
    order/ODT. Groups them into a per-user count + average."""
    by_user: dict[uuid.UUID, list[float]] = {}
    for user_id, hours in rows:
        by_user.setdefault(user_id, []).append(hours)

    kpi_rows = [
        KpiRow(user_id=user_id, count=len(values), avg_hours=sum(values) / len(values))
        for user_id, values in by_user.items()
    ]
    kpi_rows.sort(key=lambda r: r.count, reverse=True)

    all_hours = [h for _, h in rows]
    overall_avg = sum(all_hours) / len(all_hours) if all_hours else 0.0

    return KpiReport(rows=kpi_rows, overall_count=len(rows), overall_avg_hours=overall_avg)


class KpiService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_technician_kpis(self, filial_id: uuid.UUID, date_from: date, date_to: date) -> KpiReport:
        start, end = _bounds(date_from, date_to)
        result = await self.db.execute(
            select(ServiceOrder).where(
                ServiceOrder.filial_id == filial_id,
                ServiceOrder.technician_user_id.is_not(None),
                ServiceOrder.closed_at.is_not(None),
                ServiceOrder.closed_at >= start,
                ServiceOrder.closed_at <= end,
            )
        )
        orders = list(result.scalars().all())
        rows = [
            (order.technician_user_id, (order.closed_at - order.created_at).total_seconds() / 3600)
            for order in orders
            if order.technician_user_id is not None and order.closed_at is not None
        ]
        return _build_report(rows)

    async def get_advisor_kpis(self, filial_id: uuid.UUID, date_from: date, date_to: date) -> KpiReport:
        start, end = _bounds(date_from, date_to)
        result = await self.db.execute(
            select(ServiceOrder).where(
                ServiceOrder.filial_id == filial_id,
                ServiceOrder.advisor_user_id.is_not(None),
                ServiceOrder.closed_at.is_not(None),
                ServiceOrder.closed_at >= start,
                ServiceOrder.closed_at <= end,
            )
        )
        orders = list(result.scalars().all())
        rows = [
            (order.advisor_user_id, (order.closed_at - order.created_at).total_seconds() / 3600)
            for order in orders
            if order.advisor_user_id is not None and order.closed_at is not None
        ]
        return _build_report(rows)

    async def get_warehouse_kpis(self, filial_id: uuid.UUID, date_from: date, date_to: date) -> KpiReport:
        """Ranks almacenistas by the ODTs (service-order transfers) they fulfilled —
        i.e. marked as 'Pedido' — not by warehouse-to-warehouse transfers in Almacén."""
        start, end = _bounds(date_from, date_to)
        result = await self.db.execute(
            select(ServiceOrderTransfer)
            .join(ServiceOrder, ServiceOrder.id == ServiceOrderTransfer.service_order_id)
            .where(
                ServiceOrder.filial_id == filial_id,
                ServiceOrderTransfer.fulfilled_by_user_id.is_not(None),
                ServiceOrderTransfer.fulfilled_at.is_not(None),
                ServiceOrderTransfer.fulfilled_at >= start,
                ServiceOrderTransfer.fulfilled_at <= end,
            )
        )
        transfers = list(result.scalars().all())
        rows = [
            (
                transfer.fulfilled_by_user_id,
                (transfer.fulfilled_at - transfer.created_at).total_seconds() / 3600,
            )
            for transfer in transfers
            if transfer.fulfilled_by_user_id is not None and transfer.fulfilled_at is not None
        ]
        return _build_report(rows)