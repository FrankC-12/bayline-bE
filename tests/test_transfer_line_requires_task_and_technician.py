"""An advisor can't request parts from almacén before the ODS has at least
one task defined and a técnico assigned — otherwise nobody's clear on what
work needs the part or who's doing it. Checked at the HTTP layer only (not
inside ServiceOrderService.add_transfer_line itself), so the automatic
warranty-claim-to-order conversion — which can add a transfer line before a
técnico is ever assigned — stays unaffected (see test_rework_auto_supplier_claim.py)."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from test_part_sales_fifo import inventory as fifo_inventory
from test_parts_discounts import order_inventory as discount_order_inventory

from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.service_orders import router as routes
from app.modules.service_orders.models import ServiceOrderTask

inventory = fifo_inventory
order_inventory = discount_order_inventory


def _make_client(service, monkeypatch):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    register_exception_handlers(app)
    app.dependency_overrides[routes.get_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=uuid.uuid4())

    async def allowed(*args, **kwargs):
        pass

    monkeypatch.setattr(routes, "_ensure_access", allowed)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_requesting_a_part_with_no_task_and_no_technician_is_rejected(order_inventory, monkeypatch):
    service, _session, order, part_id, _lots = order_inventory

    async with _make_client(service, monkeypatch) as client:
        response = await client.post(
            f"/api/v1/service-orders/{order.id}/transfers/lines",
            json={"part_id": str(part_id), "quantity": 1},
        )

    assert response.status_code == 400
    body = response.json()
    assert "una tarea agregada" in body["message"]
    assert "un técnico asignado" in body["message"]


@pytest.mark.asyncio
async def test_requesting_a_part_with_a_task_but_no_technician_names_only_that(order_inventory, monkeypatch):
    service, session, order, part_id, _lots = order_inventory
    session.add(
        ServiceOrderTask(
            service_order_id=order.id, tempario_id=uuid.uuid4(), code_snapshot="MO",
            name_snapshot="Trabajo", hours_snapshot=1,
        )
    )
    session.commit()

    async with _make_client(service, monkeypatch) as client:
        response = await client.post(
            f"/api/v1/service-orders/{order.id}/transfers/lines",
            json={"part_id": str(part_id), "quantity": 1},
        )

    assert response.status_code == 400
    body = response.json()
    assert "una tarea agregada" not in body["message"]
    assert "un técnico asignado" in body["message"]


@pytest.mark.asyncio
async def test_requesting_a_part_with_a_task_and_a_technician_succeeds(order_inventory, monkeypatch):
    service, session, order, part_id, _lots = order_inventory
    session.add(
        ServiceOrderTask(
            service_order_id=order.id, tempario_id=uuid.uuid4(), code_snapshot="MO",
            name_snapshot="Trabajo", hours_snapshot=1,
        )
    )
    order.technician_user_id = uuid.uuid4()
    session.commit()

    async with _make_client(service, monkeypatch) as client:
        response = await client.post(
            f"/api/v1/service-orders/{order.id}/transfers/lines",
            json={"part_id": str(part_id), "quantity": 1},
        )

    assert response.status_code == 201
