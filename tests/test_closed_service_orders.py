"""Terminal orders reject writes and invoices never reprice on read."""

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from test_part_sales_fifo import inventory as fifo_inventory
from test_parts_discounts import order_inventory as discount_order_inventory

from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.inspections import router as inspection_routes
from app.modules.inspections.models import PreliminaryInspection
from app.modules.inspections.service import InspectionService
from app.modules.parts.models import Part
from app.modules.post_ventas.models import LaborSettings
from app.modules.service_orders import router as routes
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.exceptions import ServiceOrderReadOnlyError
from app.modules.service_orders.models import ServiceOrderTask, ServiceOrderTransfer, Upsell
from app.modules.service_orders.schemas import ServiceOrderUpdate

inventory = fifo_inventory
order_inventory = discount_order_inventory


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ServiceOrderStatus.ORDEN_CERRADA, ServiceOrderStatus.CANCELADO])
async def test_every_order_mutation_is_rejected_by_http(order_inventory, monkeypatch, status):
    service, session, order, part_id, _ = order_inventory
    transfer = await service.add_transfer_line(order.id, part_id, 1)
    task = ServiceOrderTask(
        service_order_id=order.id,
        tempario_id=uuid.uuid4(),
        code_snapshot="MO",
        name_snapshot="Trabajo",
        hours_snapshot=1,
    )
    upsell = Upsell(service_order_id=order.id, title="Extra", description="Trabajo adicional")
    inspection = PreliminaryInspection(
        filial_id=order.filial_id,
        vehicle_id=order.vehicle_id,
        inspector_user_id=uuid.uuid4(),
        service_order_id=order.id,
    )
    unlinked = PreliminaryInspection(
        filial_id=order.filial_id, vehicle_id=order.vehicle_id, inspector_user_id=uuid.uuid4()
    )
    session.add_all([task, upsell, inspection, unlinked])
    order.status = status
    order.total_amount = 0
    session.commit()
    stock_before = session.get(Part, part_id).stock_quantity
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.include_router(inspection_routes.router, prefix="/api/v1")
    register_exception_handlers(app)
    app.dependency_overrides[routes.get_service] = lambda: service
    app.dependency_overrides[inspection_routes.get_service] = lambda: InspectionService(service.db)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=uuid.uuid4())

    async def allowed(*args, **kwargs):
        pass

    monkeypatch.setattr(routes, "_ensure_access", allowed)
    monkeypatch.setattr(inspection_routes, "_ensure_access", allowed)
    order_url = f"/api/v1/service-orders/{order.id}"
    requests = [
        ("PATCH", order_url, {}),
        ("PATCH", order_url, {"technician_user_id": str(uuid.uuid4())}),
        ("PATCH", order_url, {"bay_id": str(uuid.uuid4())}),
        ("PATCH", order_url, {"advisor_user_id": str(uuid.uuid4())}),
        ("PATCH", order_url, {"discount_label": "Precio de costo"}),
        ("PATCH", order_url, {"notes": "Cambio"}),
        ("PATCH", order_url, {"status": "pendiente"}),
        ("PATCH", order_url, {"scheduled_at": "2026-09-10T10:00:00Z"}),
        ("DELETE", order_url, None),
        ("POST", order_url + "/tasks", {"tempario_id": str(uuid.uuid4())}),
        ("POST", order_url + "/transfers/lines", {"part_id": str(part_id), "quantity": 1}),
        ("PATCH", f"/api/v1/service-order-tasks/{task.id}", {"status": "completada"}),
        ("DELETE", f"/api/v1/service-order-tasks/{task.id}", None),
        ("POST", f"/api/v1/service-order-transfers/{transfer.id}/mark-ordered", None),
        (
            "POST",
            order_url + "/upsells",
            {"title": "Nuevo", "description": "Extra", "parts": [{"part_id": str(part_id), "quantity": 1}]},
        ),
        ("PATCH", f"/api/v1/upsells/{upsell.id}", {"status": "aprobado", "approval_channel": "presencial"}),
        ("PATCH", f"/api/v1/inspections/{inspection.id}", {"notes": "Cambio"}),
        ("PATCH", f"/api/v1/inspections/{inspection.id}", {"clear_service_order": True}),
        ("PATCH", f"/api/v1/inspections/{unlinked.id}", {"service_order_id": str(order.id)}),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for method, url, body in requests:
            response = await client.request(method, url, json=body)
            assert response.status_code == 409, (method, url, response.text)
            assert "service_order_read_only" in response.text
        assert (await client.get(order_url)).status_code == 200
        summary = await client.get(order_url + "/summary")
        assert summary.status_code == 200
        if status == ServiceOrderStatus.ORDEN_CERRADA:
            assert summary.json()["total"] == 0
            assert summary.json()["parts_subtotal"] is None
            assert summary.json()["pricing_snapshot_available"] is False
    assert session.get(Part, part_id).stock_quantity == stock_before
    assert session.scalar(select(func.count()).select_from(ServiceOrderTransfer)) == 1
    assert session.scalar(select(func.count()).select_from(ServiceOrderTask)) == 1
    assert session.scalar(select(func.count()).select_from(Upsell)) == 1
    assert order.total_amount == 0
    assert inspection.service_order_id == order.id
    assert unlinked.service_order_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("with_lines", [False, True])
async def test_invoice_snapshot_survives_settings_and_catalog_changes(order_inventory, with_lines):
    service, session, order, part_id, lots = order_inventory
    if with_lines:
        await service.add_transfer_line(order.id, part_id, 1)
        session.add(
            ServiceOrderTask(
                service_order_id=order.id,
                tempario_id=uuid.uuid4(),
                code_snapshot="MO",
                name_snapshot="Trabajo",
                hours_snapshot=2,
            )
        )
    settings = LaborSettings(filial_id=order.filial_id, hourly_rate=25, iva_percentage=16)
    session.add(settings)
    order.status = ServiceOrderStatus.COMPLETADO
    session.commit()
    from billing_support import configure_billing, invoice_payload

    billing, accounts, _ = configure_billing(service, session, order, igtf=0)
    await billing.issue(order.id, await invoice_payload(billing, order, accounts), None)
    closed = await service.close_order(order.id)
    before = (await service.get_order_summary(order.id)).model_dump(mode="json")
    assert before["pricing_frozen"] is True
    assert before["pricing_snapshot_available"] is True
    assert before["total"] == float(closed.total_amount)
    assert closed.total_amount == (Decimal("76.10") if with_lines else Decimal("0.00"))
    settings.hourly_rate = 999
    settings.iva_percentage = 25
    lots[-1].unit_cost = 999
    session.commit()
    session.expire_all()
    after = (await service.get_order_summary(order.id)).model_dump(mode="json")
    assert before == after
    assert (await service.get_order(order.id)).pricing_snapshot == before
    with pytest.raises(ServiceOrderReadOnlyError):
        await service._get_or_create_pending_transfer(order.id)
    with pytest.raises(ServiceOrderReadOnlyError):
        await service.update_order(
            order.id, ServiceOrderUpdate(status=ServiceOrderStatus.ORDEN_CERRADA)
        )


@pytest.mark.asyncio
async def test_guard_refreshes_a_previously_loaded_order(order_inventory):
    from sqlalchemy import update

    from app.modules.service_orders.models import ServiceOrder

    service, session, order, _, _ = order_inventory
    # Emulate a close committed after the route's initial permission/read check.
    session.execute(
        update(ServiceOrder)
        .where(ServiceOrder.id == order.id)
        .values(status=ServiceOrderStatus.ORDEN_CERRADA)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    assert order.status == ServiceOrderStatus.PENDIENTE
    with pytest.raises(ServiceOrderReadOnlyError):
        await service.update_order(order.id, ServiceOrderUpdate(notes="stale request"))
