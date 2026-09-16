"""Service and persistence regression tests using an isolated SQLite database.

SQLite exercises real ORM queries/persistence; PostgreSQL row locks require a
separate PostgreSQL integration environment.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exceptions import BadRequestError
from app.modules.parts.enums import PartSaleStatus
from app.modules.parts.exceptions import DispatchQuantityMismatchError, DispatchQuantityRequiredError
from app.modules.parts.models import Part, PartSale, PartSaleLine, PartWarranty
from app.modules.parts.schemas import (
    PartSaleCreate,
    PartSaleLineDispatch,
    PartSaleQuoteRead,
    PartSaleRead,
)
from app.modules.parts.service import DEFAULT_PART_WARRANTY_DAYS, PartsService
from app.modules.post_ventas.models import LaborSettings
from app.modules.warehouse.exceptions import InsufficientStockError
from app.modules.warehouse.models import PartLot, StockMovement, Warehouse


class AsyncAdapter:
    """Run the async service against a real synchronous SQLAlchemy test session."""

    def __init__(self, session):
        self.session = session

    async def execute(self, query):
        return self.session.execute(query)

    async def get(self, model, key, **kwargs):
        return self.session.get(model, key, **kwargs)

    def add(self, value):
        self.session.add(value)

    async def delete(self, value):
        self.session.delete(value)

    async def flush(self):
        self.session.flush()

    async def commit(self):
        self.session.commit()

    async def refresh(self, value, **kwargs):
        self.session.refresh(value, **kwargs)

    async def rollback(self):
        self.session.rollback()


@pytest.fixture
def inventory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial = uuid.uuid4()
        warehouse = Warehouse(id=uuid.uuid4(), filial_id=filial, name="Principal")
        other = Warehouse(id=uuid.uuid4(), filial_id=filial, name="Otro")
        part = Part(
            id=uuid.uuid4(),
            category_id=uuid.uuid4(),
            filial_id=filial,
            code="FIFO",
            name="Repuesto",
            price=0,
            stock_quantity=130,
        )
        session.add_all([warehouse, other, part])
        lots = [
            PartLot(
                id=uuid.uuid4(),
                filial_id=filial,
                warehouse_id=warehouse.id,
                part_id=part.id,
                quantity_received=10,
                quantity_remaining=10,
                unit_cost=cost,
                received_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i),
            )
            for i, cost in enumerate([10, 20, 12])
        ]
        session.add_all(lots)
        session.add(
            PartLot(
                filial_id=filial,
                warehouse_id=other.id,
                part_id=part.id,
                quantity_received=100,
                quantity_remaining=100,
                unit_cost=1,
                received_at=datetime(2025, 1, 1, tzinfo=UTC),
            )
        )
        session.commit()
        payload = dict(
            filial_id=filial,
            warehouse_id=warehouse.id,
            client_name="Cliente",
            lines=[dict(part_id=part.id, quantity=1)],
        )
        yield PartsService(AsyncAdapter(session)), session, payload, lots, other
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quantity,total,cost",
    [
        (1, 13, 10),
        (5, 65, 10),
        (10, 130, 10),
        (15, 260, Decimal("13.333333")),
        (20, 390, 15),
        (30, 546, 14),
    ],
)
async def test_quote_and_persist_fifo(inventory, quantity, total, cost):
    service, session, data, lots, _ = inventory
    data["lines"][0]["quantity"] = quantity
    payload = PartSaleCreate(**data)
    quote = PartSaleQuoteRead.model_validate(await service.quote_sale(payload))
    assert quote.total == total
    assert [lot.quantity_remaining for lot in lots] == [10, 10, 10]
    sale = await service.create_sale(payload)
    session.expire_all()
    sale = await service.get_sale(sale.id)
    read = PartSaleRead.model_validate(sale)
    assert read.total == total
    assert len(sale.lines) == 1
    line = sale.lines[0]
    assert line.unit_cost == cost
    assert line.warehouse_id == payload.warehouse_id
    assert line.line_total == total
    assert round(line.unit_price * quantity, 2) == line.line_total
    assert sum(a.quantity for a in line.allocations) == quantity
    assert sum(a.quantity * a.unit_cost for a in line.allocations) * Decimal("1.30") == total
    assert sum(lot.quantity_remaining for lot in lots) == 30 - quantity
    assert session.scalar(select(func.sum(StockMovement.quantity))) == quantity


@pytest.mark.asyncio
async def test_insufficient_stock_is_atomic_and_warehouse_scoped(inventory):
    service, session, data, lots, _ = inventory
    data["lines"][0]["quantity"] = 31
    with pytest.raises(InsufficientStockError):
        await service.create_sale(PartSaleCreate(**data))
    assert [lot.quantity_remaining for lot in lots] == [10, 10, 10]
    assert session.scalar(select(func.count()).select_from(PartSale)) == 0
    assert session.scalar(select(func.count()).select_from(PartSaleLine)) == 0


@pytest.mark.asyncio
async def test_duplicate_parts_are_merged_and_next_sale_uses_remaining_lots(inventory):
    service, _, data, _, _ = inventory
    data["lines"] *= 2
    data["lines"] = [dict(part_id=data["lines"][0]["part_id"], quantity=q) for q in [10, 5]]
    sale = await service.create_sale(PartSaleCreate(**data))
    assert len(sale.lines) == 1
    assert sale.total == 260
    data["lines"] = [dict(part_id=data["lines"][0]["part_id"], quantity=10)]
    sale = await service.create_sale(PartSaleCreate(**data))
    assert sale.total == 208  # Remaining 5 at $20 and 5 at $12.


@pytest.mark.asyncio
async def test_cancel_restores_original_lots_once(inventory):
    service, _, data, lots, _ = inventory
    data["lines"][0]["quantity"] = 15
    sale = await service.create_sale(PartSaleCreate(**data))
    await service.update_sale_status(sale.id, PartSaleStatus.CANCELADO)
    await service.update_sale_status(sale.id, PartSaleStatus.CANCELADO)
    assert [lot.quantity_remaining for lot in lots] == [10, 10, 10]


@pytest.mark.asyncio
async def test_rejects_foreign_or_inactive_warehouse(inventory):
    service, session, data, _, other = inventory
    other.filial_id = uuid.uuid4()
    session.commit()
    data["warehouse_id"] = other.id
    with pytest.raises(BadRequestError):
        await service.quote_sale(PartSaleCreate(**data))


@pytest.mark.asyncio
async def test_fractional_unit_price_does_not_round_total_early(inventory):
    service, session, data, lots, _ = inventory
    lots[0].unit_cost = Decimal("10.01")
    session.commit()
    data["lines"][0]["quantity"] = 15
    sale = await service.create_sale(PartSaleCreate(**data))
    assert sale.total == 260.13
    assert round(sale.lines[0].unit_price * 15, 2) == Decimal("260.13")


@pytest.mark.asyncio
async def test_http_quote_and_create_contract(inventory, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core.exception_handlers import register_exception_handlers
    from app.modules.auth.dependencies import get_current_user
    from app.modules.parts import router as routes

    service, _, data, _, _ = inventory
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    register_exception_handlers(app)
    app.dependency_overrides[routes.get_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: object()

    async def allowed(*args, **kwargs):
        pass

    monkeypatch.setattr(routes, "_ensure_access", allowed)
    data["lines"][0]["quantity"] = 15
    body = PartSaleCreate(**data).model_dump(mode="json")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        quoted = await client.post("/api/v1/part-sales/quote", json=body)
        assert quoted.status_code == 200
        assert quoted.json()["total"] == 260
        created = await client.post("/api/v1/part-sales", json=body)
        assert created.status_code == 201
        line = created.json()["lines"][0]
        assert line["warehouse_id"] == body["warehouse_id"]
        assert line["line_total"] == 260
        assert len(line["allocations"]) == 2
        assert round(line["unit_price"] * 15, 2) == line["line_total"]
        del body["warehouse_id"]
        missing = await client.post("/api/v1/part-sales", json=body)
        assert missing.status_code == 422


@pytest.mark.asyncio
async def test_dispatch_matching_quantity_advances_to_pedido(inventory):
    service, _session, data, _lots, _other = inventory
    sale = await service.create_sale(PartSaleCreate(**data))
    line = sale.lines[0]

    updated = await service.update_sale_status(
        sale.id,
        PartSaleStatus.PEDIDO,
        [PartSaleLineDispatch(line_id=line.id, dispatched_quantity=line.quantity)],
    )

    assert updated.status == PartSaleStatus.PEDIDO
    assert updated.lines[0].dispatched_quantity == line.quantity


@pytest.mark.asyncio
async def test_dispatch_mismatch_blocks_transition_and_can_be_retried(inventory):
    service, _session, data, _lots, _other = inventory
    sale = await service.create_sale(PartSaleCreate(**data))
    line = sale.lines[0]

    with pytest.raises(DispatchQuantityMismatchError):
        await service.update_sale_status(
            sale.id,
            PartSaleStatus.PEDIDO,
            [PartSaleLineDispatch(line_id=line.id, dispatched_quantity=line.quantity - 1)],
        )
    sale = await service.get_sale(sale.id)
    assert sale.status == PartSaleStatus.PENDIENTE

    updated = await service.update_sale_status(
        sale.id,
        PartSaleStatus.PEDIDO,
        [PartSaleLineDispatch(line_id=line.id, dispatched_quantity=line.quantity)],
    )
    assert updated.status == PartSaleStatus.PEDIDO


@pytest.mark.asyncio
async def test_dispatch_missing_lines_is_rejected(inventory):
    service, _session, data, _lots, _other = inventory
    sale = await service.create_sale(PartSaleCreate(**data))

    with pytest.raises(DispatchQuantityRequiredError):
        await service.update_sale_status(sale.id, PartSaleStatus.PEDIDO, None)
    with pytest.raises(DispatchQuantityRequiredError):
        await service.update_sale_status(sale.id, PartSaleStatus.PEDIDO, [])


@pytest.mark.asyncio
async def test_pedido_to_completado_still_works(inventory):
    service, _session, data, _lots, _other = inventory
    sale = await service.create_sale(PartSaleCreate(**data))
    line = sale.lines[0]
    await service.update_sale_status(
        sale.id,
        PartSaleStatus.PEDIDO,
        [PartSaleLineDispatch(line_id=line.id, dispatched_quantity=line.quantity)],
    )

    completed = await service.update_sale_status(sale.id, PartSaleStatus.COMPLETADO)

    assert completed.status == PartSaleStatus.COMPLETADO


@pytest.mark.asyncio
async def test_completado_creates_counter_warranty_with_default_days(inventory):
    service, session, data, _lots, _other = inventory
    sale = await service.create_sale(PartSaleCreate(**data))
    line = sale.lines[0]
    await service.update_sale_status(
        sale.id,
        PartSaleStatus.PEDIDO,
        [PartSaleLineDispatch(line_id=line.id, dispatched_quantity=line.quantity)],
    )

    completed = await service.update_sale_status(sale.id, PartSaleStatus.COMPLETADO)

    warranties = session.scalars(
        select(PartWarranty).where(PartWarranty.part_sale_line_id == completed.lines[0].id)
    ).all()
    assert len(warranties) == 1
    warranty = warranties[0]
    assert warranty.warranty_days == DEFAULT_PART_WARRANTY_DAYS
    assert warranty.quantity == line.quantity
    assert warranty.lot_id == line.allocations[0].lot_id
    assert warranty.expires_at - warranty.starts_at == timedelta(days=DEFAULT_PART_WARRANTY_DAYS)
    assert warranty.is_active


@pytest.mark.asyncio
async def test_completado_creates_one_warranty_per_lot_allocation(inventory):
    service, session, data, _lots, _other = inventory
    data["lines"][0]["quantity"] = 15  # Spans two lots: 10 @ $10 + 5 @ $20.
    sale = await service.create_sale(PartSaleCreate(**data))
    line = sale.lines[0]
    await service.update_sale_status(
        sale.id,
        PartSaleStatus.PEDIDO,
        [PartSaleLineDispatch(line_id=line.id, dispatched_quantity=line.quantity)],
    )

    completed = await service.update_sale_status(sale.id, PartSaleStatus.COMPLETADO)

    warranties = session.scalars(
        select(PartWarranty).where(PartWarranty.part_sale_line_id == completed.lines[0].id)
    ).all()
    assert len(warranties) == 2
    assert {w.lot_id for w in warranties} == {a.lot_id for a in line.allocations}
    assert sum(w.quantity for w in warranties) == 15


@pytest.mark.asyncio
async def test_completado_uses_configured_warranty_days(inventory):
    service, session, data, _lots, _other = inventory
    filial_id = data["filial_id"]
    session.add(LaborSettings(filial_id=filial_id, part_warranty_days=30))
    session.commit()

    sale = await service.create_sale(PartSaleCreate(**data))
    line = sale.lines[0]
    await service.update_sale_status(
        sale.id,
        PartSaleStatus.PEDIDO,
        [PartSaleLineDispatch(line_id=line.id, dispatched_quantity=line.quantity)],
    )

    completed = await service.update_sale_status(sale.id, PartSaleStatus.COMPLETADO)

    warranty = session.scalars(
        select(PartWarranty).where(PartWarranty.part_sale_line_id == completed.lines[0].id)
    ).one()
    assert warranty.warranty_days == 30
    assert warranty.expires_at - warranty.starts_at == timedelta(days=30)
