import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from test_workshop_dispatch_postgres import inventory

from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part
from app.modules.warehouse.models import PartLot, StockMovement, Transfer
from app.modules.warehouse.router import router


@pytest.mark.asyncio
async def test_inventory_mutations_reject_foreign_resources_before_any_write(billing_db):
    ctx = billing_db
    own_part, own_warehouse, own_second = await inventory(ctx, local=10)
    async with ctx.sessions() as db:
        own_filial = await db.get(Filial, ctx.filial_id)
        foreign_filial = Filial(
            holding_id=own_filial.holding_id, name="Otra filial", slug=str(uuid.uuid4())
        )
        db.add(foreign_filial)
        await db.commit()
        foreign_id = foreign_filial.id
    foreign_part, foreign_warehouse, _ = await inventory(
        SimpleNamespace(sessions=ctx.sessions, filial_id=foreign_id), local=10
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    register_exception_handlers(app)
    user = CurrentUser(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role_id=uuid.uuid4(),
        role_slug="filial-admin",
        scope="filial",
        filial_id=ctx.filial_id,
        holding_id=None,
    )
    app.dependency_overrides[get_current_user] = lambda: user

    async def session():
        async with ctx.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session

    def stock_in(warehouse, part):
        return {
            "filial_id": str(ctx.filial_id),
            "warehouse_id": str(warehouse),
            "lines": [{"part_id": str(part), "quantity": 1, "unit_cost": 10}],
        }

    cases = [
        ("POST", "/almacen/stock-in", stock_in(foreign_warehouse, own_part)),
        ("POST", "/almacen/stock-in", stock_in(own_warehouse, foreign_part)),
        (
            "POST",
            "/almacen/stock-in",
            {
                **stock_in(own_warehouse, own_part),
                "lines": [
                    {"part_id": str(own_part), "quantity": 1, "unit_cost": 10},
                    {"part_id": str(foreign_part), "quantity": 1, "unit_cost": 10},
                ],
            },
        ),
        (
            "POST",
            "/almacen/stock-out",
            {
                "filial_id": str(ctx.filial_id),
                "warehouse_id": str(foreign_warehouse),
                "part_id": str(foreign_part),
                "quantity": 1,
            },
        ),
        (
            "POST",
            "/almacen/transfers",
            {
                "filial_id": str(ctx.filial_id),
                "origin_warehouse_id": str(own_warehouse),
                "destination_warehouse_id": str(foreign_warehouse),
                "lines": [{"part_id": str(own_part), "quantity": 1}],
            },
        ),
        (
            "POST",
            "/almacen/transfers",
            {
                "filial_id": str(ctx.filial_id),
                "origin_warehouse_id": str(own_warehouse),
                "destination_warehouse_id": str(own_second),
                "lines": [{"part_id": str(foreign_part), "quantity": 1}],
            },
        ),
        (
            "PATCH",
            f"/almacen/inventory/location?filial_id={ctx.filial_id}",
            {
                "warehouse_id": str(foreign_warehouse),
                "part_id": str(foreign_part),
                "location": "NO CAMBIAR",
            },
        ),
        (
            "POST",
            "/almacen/stock-in/bulk",
            {
                "filial_id": str(ctx.filial_id),
                "warehouse_id": str(foreign_warehouse),
                "items": [
                    {
                        "part_code": "FORBIDDEN",
                        "part_name": "No crear",
                        "quantity": 1,
                        "unit_cost": 1,
                    }
                ],
            },
        ),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        for method, path, payload in cases:
            response = await http.request(method, "/api/v1" + path, json=payload)
            assert response.status_code == 403, (path, response.text)
            assert response.json()["errorCode"] == "resource_filial_mismatch"
        missing = await http.post("/api/v1/almacen/stock-in", json=stock_in(uuid.uuid4(), own_part))
        assert missing.status_code == 404
    async with ctx.sessions() as db:
        for part_id in (own_part, foreign_part):
            assert (await db.get(Part, part_id)).stock_quantity == 10
            assert (
                await db.execute(
                    select(func.sum(PartLot.quantity_remaining)).where(PartLot.part_id == part_id)
                )
            ).scalar() == 10
        assert (
            await db.execute(
                select(func.count())
                .select_from(StockMovement)
                .where(StockMovement.filial_id.in_([ctx.filial_id, foreign_id]))
            )
        ).scalar() == 0
        assert (
            await db.execute(
                select(func.count())
                .select_from(Transfer)
                .where(Transfer.filial_id == ctx.filial_id)
            )
        ).scalar() == 0
        assert (
            await db.execute(
                select(func.count())
                .select_from(Part)
                .where(Part.filial_id == ctx.filial_id, Part.code == "FORBIDDEN")
            )
        ).scalar() == 0
