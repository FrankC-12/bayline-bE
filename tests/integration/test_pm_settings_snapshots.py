"""Settings affect future orders; manual BCV mode is explicit and reversible."""

import uuid

import pytest
from sqlalchemy import select

from app.modules.ajustes.router import patch_settings, restore_bcv
from app.modules.ajustes.snapshots import settings_snapshot
from app.modules.auth.schemas import CurrentUser
from app.modules.post_ventas.models import LaborSettings
from app.modules.post_ventas.schemas import SettingsPatch
from app.modules.post_ventas.service import PostVentasService
from app.modules.roles.enums import RoleScope
from app.modules.service_orders.billing import BillingService
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.service import ServiceOrderService


@pytest.mark.asyncio
async def test_partial_settings_patch_does_not_reprice_existing_order(billing_db):
    ctx = billing_db
    async with ctx.sessions() as db:
        settings = (
            await db.execute(select(LaborSettings).where(LaborSettings.filial_id == ctx.filial_id))
        ).scalar_one()
        order = await db.get(ServiceOrder, ctx.order_id)
        order.settings_snapshot = settings_snapshot(settings)
        await db.commit()
        before = await ServiceOrderService(db).get_order_summary(order.id)
        user = CurrentUser(
            user_id=uuid.uuid4(),
            email="qa@example.com",
            full_name="QA",
            role_id=uuid.uuid4(),
            role_slug="filial-admin",
            scope=RoleScope.FILIAL,
            holding_id=None,
            filial_id=ctx.filial_id,
        )
        updated = await patch_settings(
            SettingsPatch(hourly_rate=88, iva_percentage=20, bcv_rate=99),
            ctx.filial_id,
            user,
            PostVentasService(db),
        )
        assert updated.bcv_rate_mode == "manual"
        assert updated.commission_percentage == 30
        assert set(updated.field_changes) == {"hourly_rate", "iva_percentage", "bcv_rate"}
        after = await ServiceOrderService(db).get_order_summary(order.id)
        assert after.total == before.total
        assert after.hourly_rate == 25 and after.iva_percentage == 16
        context = await BillingService(db).context(order.id)
        assert context["bcv_rate"] == 99
        restored = await restore_bcv(ctx.filial_id, user, PostVentasService(db))
        assert restored.bcv_rate_mode == "automatic"
        assert float(restored.bcv_rate) != 99


@pytest.mark.asyncio
async def test_new_workshop_warranty_starts_on_close_and_uses_departure_mileage(billing_db):
    from conftest import invoice_input
    from sqlalchemy import func

    from app.modules.clients.models import Vehicle
    from app.modules.post_ventas.models import WorkshopWarranty
    from app.modules.service_orders.models import ServiceOrderTask

    ctx = billing_db
    async with ctx.sessions() as db:
        order = await db.get(ServiceOrder, ctx.order_id)
        vehicle = await db.get(Vehicle, order.vehicle_id)
        vehicle.vin = "1HGCM82633A004352"
        order.intake_mileage = 10000
        settings = (
            await db.execute(select(LaborSettings).where(LaborSettings.filial_id == ctx.filial_id))
        ).scalar_one()
        order.settings_snapshot = settings_snapshot(settings)
        task = (
            await db.execute(
                select(ServiceOrderTask).where(ServiceOrderTask.service_order_id == order.id)
            )
        ).scalar_one()
        task.warranty_snapshot = {
            "labor": {
                "name": "Personalizada",
                "duration_days": 30,
                "duration_km": 1000,
                "covered_by": "la_casa",
            },
            "parts": {
                "name": "Personalizada",
                "duration_days": 30,
                "duration_km": 1000,
                "covered_by": "la_casa",
            },
        }
        await db.commit()
    payload = await invoice_input(ctx)
    async with ctx.sessions() as db:
        await BillingService(db).issue(ctx.order_id, payload, None)
        assert (
            await db.execute(
                select(func.count())
                .select_from(WorkshopWarranty)
                .where(WorkshopWarranty.service_order_id == ctx.order_id)
            )
        ).scalar() == 0
        await ServiceOrderService(db).close_order(ctx.order_id, exit_mileage=10050)
        warranty = (
            await db.execute(
                select(WorkshopWarranty).where(WorkshopWarranty.service_order_id == ctx.order_id)
            )
        ).scalar_one()
        assert warranty.duration_days == 30 and warranty.duration_km == 1000
        assert warranty.expiration_mileage == 11050
