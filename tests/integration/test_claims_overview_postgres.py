"""PostgreSQL precision and Caracas boundaries for the claims dashboard."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.modules.service_orders import claims_workflow
from app.modules.service_orders.claims_workflow import ClaimsWorkflow
from app.modules.service_orders.enums import (
    ServiceOrderStatus,
    TaskStatus,
    WarrantyClaimStatus,
    WarrantyClaimType,
)
from app.modules.service_orders.models import (
    ServiceOrder,
    ServiceOrderTask,
    ServiceOrderTransfer,
    ServiceOrderTransferLine,
    WarrantyClaim,
)
from tests.integration.test_workshop_dispatch_postgres import inventory


@pytest.mark.asyncio
async def test_overview_keeps_numeric_costs_separate_and_month_boundaries_in_caracas(
    billing_db, monkeypatch
):
    ctx = billing_db
    monkeypatch.setattr(claims_workflow, "venezuela_today", lambda: date(2026, 10, 19))
    part_id, _, _ = await inventory(ctx)
    async with ctx.sessions() as db:
        source = await db.get(ServiceOrder, ctx.order_id)
        catalog = (
            await db.execute(
                select(ServiceOrderTask).where(ServiceOrderTask.service_order_id == source.id)
            )
        ).scalar_one()
        source.status = ServiceOrderStatus.ORDEN_CERRADA
        source.closed_at = datetime(2026, 10, 1, 3, 59, 59, tzinfo=UTC)
        for sequence, converted in [
            (10, datetime(2026, 10, 1, 4, tzinfo=UTC)),
            (11, datetime(2026, 10, 1, 3, 59, 59, tzinfo=UTC)),
        ]:
            redo = ServiceOrder(
                filial_id=ctx.filial_id,
                vehicle_id=source.vehicle_id,
                order_type_id=source.order_type_id,
                sequence_number=sequence,
                settings_snapshot={"hourly_rate": 25},
            )
            db.add(redo)
            await db.flush()
            db.add(
                WarrantyClaim(
                    filial_id=ctx.filial_id,
                    sequence_number=sequence,
                    vehicle_id=source.vehicle_id,
                    service_order_id=source.id,
                    resulting_service_order_id=redo.id,
                    claim_type=WarrantyClaimType.COMEBACK,
                    reported_mileage=0,
                    claimed_at=date(2026, 10, 1),
                    converted_at=converted,
                    status=WarrantyClaimStatus.CONVERTIDO_A_ODS,
                )
            )
            for hours, status in [
                (1.5, TaskStatus.COMPLETADA),
                (2, TaskStatus.COMPLETADA),
                (10, TaskStatus.PENDIENTE),
            ]:
                db.add(
                    ServiceOrderTask(
                        service_order_id=redo.id,
                        tempario_id=catalog.tempario_id,
                        code_snapshot=catalog.code_snapshot,
                        name_snapshot=catalog.name_snapshot,
                        hours_snapshot=hours,
                        status=status,
                    )
                )
            for number, dispatched, costs in [(1, True, [10.25, 20.10]), (2, False, [900])]:
                transfer = ServiceOrderTransfer(
                    service_order_id=redo.id, sequence_number=number, stock_deducted=dispatched
                )
                db.add(transfer)
                await db.flush()
                for cost in costs:
                    db.add(
                        ServiceOrderTransferLine(
                            transfer_id=transfer.id,
                            part_id=part_id,
                            quantity=1,
                            cost_total=cost,
                            unit_price=999,
                            line_total=999,
                        )
                    )
        await db.commit()
        result = await ClaimsWorkflow(db).overview(ctx.filial_id)
        assert result["monthly_reworks"] == 1
        assert result["monthly_closed"] == 0
        assert result["monthly_cost"] == pytest.approx(117.85)
