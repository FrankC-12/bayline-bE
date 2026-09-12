import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.administracion.models import ExpenseEntry, IncomeEntry
from app.modules.clients.models import Client, Vehicle
from app.modules.kpis.schemas import (
    KpiReport,
    KpiRow,
    ManualMovementsRate,
    MaintenanceDueReport,
    MaintenanceDueRow,
    ReworkPartRow,
    ReworkReport,
    ReworkServiceRow,
    ReworkTechnicianRow,
)
from app.modules.parts.models import Part
from app.modules.post_ventas.models import Tempario
from app.modules.service_orders.models import (
    ReworkClaim,
    ServiceOrder,
    ServiceOrderInvoice,
    ServiceOrderTransfer,
)

QUICK_CLAIM_DAYS_THRESHOLD = 30


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

    async def get_maintenance_due(
        self, filial_id: uuid.UUID, window_days: int
    ) -> MaintenanceDueReport:
        """Vehicles whose advisor-suggested next-visit date falls within the
        next `window_days` — the call list to fill the workshop's agenda —
        plus a separate count of vehicles whose suggested date already
        lapsed without a newer visit resetting it."""
        today = date.today()
        horizon = today + timedelta(days=window_days)

        due_result = await self.db.execute(
            select(Vehicle, Client)
            .join(Client, Client.id == Vehicle.client_id)
            .where(
                Client.filial_id == filial_id,
                Vehicle.next_maintenance_due_at.is_not(None),
                Vehicle.next_maintenance_due_at >= today,
                Vehicle.next_maintenance_due_at <= horizon,
            )
            .order_by(Vehicle.next_maintenance_due_at)
        )
        rows = [
            MaintenanceDueRow(
                vehicle_id=vehicle.id,
                plate=vehicle.plate,
                brand=vehicle.brand,
                model=vehicle.model,
                client_id=client.id,
                client_name=client.full_name,
                phone_primary=client.phone_primary,
                phone_secondary=client.phone_secondary,
                due_at=vehicle.next_maintenance_due_at,
                days_until_due=(vehicle.next_maintenance_due_at - today).days,
            )
            for vehicle, client in due_result.all()
        ]

        overdue_result = await self.db.execute(
            select(func.count())
            .select_from(Vehicle)
            .join(Client, Client.id == Vehicle.client_id)
            .where(
                Client.filial_id == filial_id,
                Vehicle.next_maintenance_due_at.is_not(None),
                Vehicle.next_maintenance_due_at < today,
            )
        )
        overdue_count = overdue_result.scalar_one()

        return MaintenanceDueReport(window_days=window_days, rows=rows, overdue_count=overdue_count)

    async def get_rework_report(self, filial_id: uuid.UUID, date_from: date, date_to: date) -> ReworkReport:
        """Of the orders invoiced in this period (cohort by invoice date —
        not by claim date, since a claim can land well after the range
        ends), how many came back with a rework claim, by whom, on what
        service, and on what part. Data quality depends on who's actually
        recording the failure cause — see the caveat surfaced in the UI."""
        start, end = _bounds(date_from, date_to)
        pairs = (
            (
                await self.db.execute(
                    select(ServiceOrder, ServiceOrderInvoice)
                    .join(ServiceOrderInvoice, ServiceOrderInvoice.service_order_id == ServiceOrder.id)
                    .where(
                        ServiceOrder.filial_id == filial_id,
                        ServiceOrderInvoice.issued_at >= start,
                        ServiceOrderInvoice.issued_at <= end,
                    )
                )
            )
            .all()
        )
        invoiced_orders_count = len(pairs)
        issued_date_by_order = {order.id: invoice.issued_at.date() for order, invoice in pairs}
        technician_by_order = {order.id: order.technician_user_id for order, _ in pairs}
        order_ids = list(issued_date_by_order.keys())

        claims: list[ReworkClaim] = []
        if order_ids:
            claims_result = await self.db.execute(
                select(ReworkClaim).where(ReworkClaim.service_order_id.in_(order_ids))
            )
            claims = list(claims_result.scalars().all())

        days_by_claim = {
            claim.id: (claim.claimed_at - issued_date_by_order[claim.service_order_id]).days for claim in claims
        }
        orders_with_claim_ids = {claim.service_order_id for claim in claims}

        all_days = list(days_by_claim.values())
        avg_days_to_claim = sum(all_days) / len(all_days) if all_days else None
        quick_claims_count = sum(1 for d in all_days if d <= QUICK_CLAIM_DAYS_THRESHOLD)
        slow_claims_count = sum(1 for d in all_days if d > QUICK_CLAIM_DAYS_THRESHOLD)
        rework_rate = len(orders_with_claim_ids) / invoiced_orders_count if invoiced_orders_count else 0.0

        by_technician: list[ReworkTechnicianRow] = []
        technician_ids = {tech_id for tech_id in technician_by_order.values() if tech_id is not None}
        for tech_id in technician_ids:
            tech_order_ids = {oid for oid, tid in technician_by_order.items() if tid == tech_id}
            tech_claims = [c for c in claims if c.service_order_id in tech_order_ids]
            tech_orders_with_claim = tech_order_ids & orders_with_claim_ids
            tech_days = [days_by_claim[c.id] for c in tech_claims]
            by_technician.append(
                ReworkTechnicianRow(
                    user_id=tech_id,
                    invoiced_orders_count=len(tech_order_ids),
                    claims_count=len(tech_claims),
                    rework_rate=len(tech_orders_with_claim) / len(tech_order_ids) if tech_order_ids else 0.0,
                    avg_days_to_claim=sum(tech_days) / len(tech_days) if tech_days else None,
                )
            )
        by_technician.sort(key=lambda r: r.claims_count, reverse=True)

        service_ids = {c.tempario_id for c in claims if c.tempario_id is not None}
        temparios = {}
        if service_ids:
            temparios_result = await self.db.execute(select(Tempario).where(Tempario.id.in_(service_ids)))
            temparios = {t.id: t for t in temparios_result.scalars().all()}
        by_service: list[ReworkServiceRow] = []
        for tempario_id in service_ids:
            tempario_claims = [c for c in claims if c.tempario_id == tempario_id]
            tempario_days = [days_by_claim[c.id] for c in tempario_claims]
            tempario = temparios.get(tempario_id)
            by_service.append(
                ReworkServiceRow(
                    tempario_id=tempario_id,
                    tempario_name=tempario.name if tempario else "Servicio eliminado",
                    claims_count=len(tempario_claims),
                    avg_days_to_claim=sum(tempario_days) / len(tempario_days) if tempario_days else None,
                )
            )
        by_service.sort(key=lambda r: r.claims_count, reverse=True)

        part_ids = {c.part_id for c in claims if c.part_id is not None}
        parts = {}
        if part_ids:
            parts_result = await self.db.execute(select(Part).where(Part.id.in_(part_ids)))
            parts = {p.id: p for p in parts_result.scalars().all()}
        by_part: list[ReworkPartRow] = []
        for part_id in part_ids:
            part_claims = [c for c in claims if c.part_id == part_id]
            part_days = [days_by_claim[c.id] for c in part_claims]
            part = parts.get(part_id)
            by_part.append(
                ReworkPartRow(
                    part_id=part_id,
                    part_name=part.name if part else "Repuesto eliminado",
                    claims_count=len(part_claims),
                    avg_days_to_claim=sum(part_days) / len(part_days) if part_days else None,
                )
            )
        by_part.sort(key=lambda r: r.claims_count, reverse=True)

        return ReworkReport(
            invoiced_orders_count=invoiced_orders_count,
            orders_with_claim_count=len(orders_with_claim_ids),
            rework_rate=rework_rate,
            avg_days_to_claim=avg_days_to_claim,
            quick_claims_count=quick_claims_count,
            slow_claims_count=slow_claims_count,
            by_technician=by_technician,
            by_service=by_service,
            by_part=by_part,
        )

    async def get_manual_movements_rate(
        self, filial_id: uuid.UUID, date_from: date, date_to: date
    ) -> ManualMovementsRate:
        """Of the income/expense rows in this period, how many are genuinely
        manual — i.e. carry concept/counterparty_type — vs. rows created by
        a structured flow (sale, invoice collection, claim resolution,
        warranty reimbursement) that happen to reuse the same tables. A high
        rate is the alert that someone is routing money around the
        operational flows instead of through them."""
        total_count = 0
        manual_count = 0
        for model in (IncomeEntry, ExpenseEntry):
            result = await self.db.execute(
                select(func.count(), func.count(model.counterparty_type)).where(
                    model.filial_id == filial_id,
                    model.entry_date >= date_from,
                    model.entry_date <= date_to,
                )
            )
            row_total, row_manual = result.one()
            total_count += row_total
            manual_count += row_manual

        rate = manual_count / total_count if total_count else 0.0
        return ManualMovementsRate(total_count=total_count, manual_count=manual_count, rate=rate)