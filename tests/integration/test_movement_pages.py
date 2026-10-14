import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from test_workshop_dispatch_postgres import inventory

from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import BadRequestError
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.warehouse.enums import MovementType
from app.modules.warehouse.models import StockMovement
from app.modules.warehouse.router import router
from app.modules.warehouse.service import AlmacenService


async def seed(ctx):
    part_id, first, second = await inventory(ctx)
    async with ctx.sessions() as db:
        rows = [
            StockMovement(
                filial_id=ctx.filial_id,
                warehouse_id=warehouse,
                part_id=part_id,
                movement_type=kind,
                quantity=1,
                reference=reference,
                created_at=at,
            )
            for warehouse, kind, reference, at in (
                (first, MovementType.ENTRADA, "Compra 100%", datetime(2026, 10, 14, 3, tzinfo=UTC)),
                (first, MovementType.SALIDA, "ODS", datetime(2026, 10, 14, 4, tzinfo=UTC)),
                (
                    first,
                    MovementType.TRANSFERENCIA_SALIDA,
                    "TRF",
                    datetime(2026, 10, 14, 4, tzinfo=UTC),
                ),
                (
                    second,
                    MovementType.TRANSFERENCIA_ENTRADA,
                    "TRF",
                    datetime(2026, 10, 14, 5, tzinfo=UTC),
                ),
            )
        ]
        db.add_all(rows)
        await db.commit()
    return first, second


@pytest.mark.asyncio
async def test_movement_pages_are_bounded_stable_and_filter_in_database(billing_db):
    ctx = billing_db
    first, second = await seed(ctx)
    async with ctx.sessions() as db:
        service = AlmacenService(db)
        page1 = await service.page_movements(ctx.filial_id, page_size=2)
        page2 = await service.page_movements(ctx.filial_id, page_size=2, page=2)
        assert page1.total == page2.total == 4
        assert page1.total_pages == 2
        assert len(page1.items) == len(page2.items) == 2
        assert not {item.id for item in page1.items} & {item.id for item in page2.items}
        assert (await service.page_movements(ctx.filial_id, page_size=2)).items == page1.items
        assert all(item.part_name == "Aceite" for item in page1.items)
        transfers = await service.page_movements(ctx.filial_id, movement_type="transferencia")
        assert transfers.total == 2
        assert (await service.page_movements(ctx.filial_id, warehouse_id=second)).total == 1
        assert (await service.page_movements(ctx.filial_id, warehouse_id=first)).total == 3
        literal_search = await service.page_movements(ctx.filial_id, search="100%")
        assert literal_search.total == 1
        # UTC 03:00 is still the previous calendar day in Caracas.
        previous_day = await service.page_movements(
            ctx.filial_id, date_from=date(2026, 10, 13), date_to=date(2026, 10, 13)
        )
        assert previous_day.total == 1
        assert (
            await service.page_movements(
                ctx.filial_id, date_from=date(2026, 10, 14), date_to=date(2026, 10, 14)
            )
        ).total == 3
        assert (await service.page_movements(uuid.uuid4())).total == 0
        assert (await service.page_movements(ctx.filial_id, page_size=2, page=999)).page == 2
        with pytest.raises(BadRequestError):
            await service.page_movements(
                ctx.filial_id, date_from=date(2026, 10, 15), date_to=date(2026, 10, 13)
            )
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(db.get_bind(), "before_cursor_execute", capture)
        try:
            await service.page_movements(ctx.filial_id)
            assert len(statements) == 2
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", capture)


@pytest.mark.asyncio
async def test_paginated_endpoint_validates_limits_filters_and_filial_access(billing_db):
    ctx = billing_db
    await seed(ctx)
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
    path = f"/api/v1/almacen/movements/page?filial_id={ctx.filial_id}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get(path + "&page_size=2")
        assert response.status_code == 200
        assert len(response.json()["items"]) == 2
        for invalid in (
            "page=0",
            "page_size=0",
            "page_size=201",
            "movement_type=invalid",
            "date_from=invalid",
        ):
            assert (await http.get(path + "&" + invalid)).status_code == 422
        assert (
            await http.get(path + "&date_from=2026-10-15&date_to=2026-10-13")
        ).status_code == 400
        assert (
            await http.get(f"/api/v1/almacen/movements/page?filial_id={uuid.uuid4()}")
        ).status_code == 403
        assert (
            await http.get(f"/api/v1/almacen/movements?filial_id={ctx.filial_id}")
        ).status_code == 200
