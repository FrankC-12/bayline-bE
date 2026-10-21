"""Latest dated visits win, even with historical undated closed orders."""

from datetime import UTC, date, datetime

import pytest

from app.modules.clients.models import Vehicle
from app.modules.clients.service import ClientService
from app.modules.inspections.models import PreliminaryInspection
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.models import ServiceOrder
from tests.integration.test_mobile_workshop import technician


@pytest.mark.asyncio
async def test_latest_mileage_queries_preserve_visit_precedence_on_postgres(billing_db):
    ctx = billing_db
    actor = await technician(ctx)
    async with ctx.sessions() as db:
        order = await db.get(ServiceOrder, ctx.order_id)
        order.status = ServiceOrderStatus.ORDEN_CERRADA
        order.exit_mileage = 40000
        order.closed_at = datetime(2026, 2, 1, tzinfo=UTC)
        db.add(
            ServiceOrder(
                filial_id=ctx.filial_id,
                vehicle_id=order.vehicle_id,
                order_type_id=order.order_type_id,
                sequence_number=2,
                status=ServiceOrderStatus.ORDEN_CERRADA,
                exit_mileage=90000,
            )
        )
        db.add(
            PreliminaryInspection(
                filial_id=ctx.filial_id,
                vehicle_id=order.vehicle_id,
                inspector_user_id=actor.user_id,
                mileage=10000,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        await db.commit()
        vehicle = await db.get(Vehicle, order.vehicle_id)
        service = ClientService(db)
        await service.attach_vehicle_mileage([vehicle])
        assert vehicle.current_mileage == 40000
        assert vehicle.current_mileage_service_order_id == order.id
        assert vehicle.current_mileage_visit_date == date(2026, 1, 31)

        db.add(
            PreliminaryInspection(
                filial_id=ctx.filial_id,
                vehicle_id=order.vehicle_id,
                inspector_user_id=actor.user_id,
                mileage=35000,
                created_at=datetime(2026, 3, 1, tzinfo=UTC),
            )
        )
        await db.commit()
        await service.attach_vehicle_mileage([vehicle])
        assert vehicle.current_mileage == 35000
        assert vehicle.current_mileage_service_order_id is None
        assert vehicle.current_mileage_visit_date == date(2026, 2, 28)
