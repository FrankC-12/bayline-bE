"""Ubicación en almacén on the Parts Catalog is never typed by hand — it's
pulled from the most recently received lot that recorded one (same
"latest wins" convention as stock_total/reference_price), and is now also
capturable when receiving a Purchase Request, not just manual/bulk stock-in."""

import os
import uuid
from datetime import UTC, datetime, timedelta

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.administracion.enums import PurchaseRequestStatus, SupplierStatus, SupplierType
from app.modules.administracion.models import PurchaseRequest, PurchaseRequestLine, Supplier
from app.modules.administracion.service import AdministracionService
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part
from app.modules.parts.service import PartsService
from app.modules.warehouse.models import PartLot, Warehouse


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        warehouse = Warehouse(filial_id=filial_id, name="Principal")
        session.add(warehouse)
        session.commit()
        db = AsyncAdapter(session)
        yield PartsService(db), AdministracionService(db), session, filial_id, warehouse


@pytest.mark.asyncio
async def test_location_is_pulled_from_the_most_recently_received_lot(env):
    parts_service, _admin, session, filial_id, warehouse = env
    part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Repuesto", price=0)
    session.add(part)
    session.commit()

    older = PartLot(
        filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
        quantity_received=5, quantity_remaining=5, unit_cost=10, location="Pasillo 1",
        received_at=datetime.now(UTC) - timedelta(days=2),
    )
    newer = PartLot(
        filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
        quantity_received=5, quantity_remaining=5, unit_cost=12, location="Pasillo 3",
        received_at=datetime.now(UTC) - timedelta(days=1),
    )
    session.add_all([older, newer])
    session.commit()

    parts = await parts_service.list_parts(filial_id)
    assert parts[0].location == "Pasillo 3"


@pytest.mark.asyncio
async def test_a_more_recent_lot_with_no_location_falls_back_to_an_older_one_that_has_it(env):
    parts_service, _admin, session, filial_id, warehouse = env
    part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-2", name="Repuesto", price=0)
    session.add(part)
    session.commit()

    with_location = PartLot(
        filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
        quantity_received=5, quantity_remaining=5, unit_cost=10, location="Pasillo 1",
        received_at=datetime.now(UTC) - timedelta(days=2),
    )
    without_location = PartLot(
        filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
        quantity_received=5, quantity_remaining=5, unit_cost=12, location=None,
        received_at=datetime.now(UTC) - timedelta(days=1),
    )
    session.add_all([with_location, without_location])
    session.commit()

    parts = await parts_service.list_parts(filial_id)
    assert parts[0].location == "Pasillo 1"


@pytest.mark.asyncio
async def test_part_with_no_lots_has_no_location(env):
    parts_service, _admin, session, filial_id, _warehouse = env
    part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-3", name="Repuesto", price=0)
    session.add(part)
    session.commit()

    parts = await parts_service.list_parts(filial_id)
    assert parts[0].location is None


@pytest.mark.asyncio
async def test_receiving_a_purchase_request_can_set_a_location_on_the_new_lot(env):
    parts_service, admin_service, session, filial_id, warehouse = env
    part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-4", name="Repuesto", price=0)
    supplier = Supplier(
        filial_id=filial_id, business_name="Proveedor X", rif="J-12345678-9",
        supplier_type=SupplierType.IMPORTADOR, status=SupplierStatus.ACTIVO,
    )
    session.add_all([part, supplier])
    session.commit()

    request = PurchaseRequest(
        filial_id=filial_id, sequence_number=1, supplier_id=supplier.id,
        status=PurchaseRequestStatus.PAGADA,
    )
    session.add(request)
    session.flush()
    session.add(PurchaseRequestLine(purchase_request_id=request.id, part_id=part.id, quantity=10, unit_cost=5))
    session.commit()

    await admin_service.update_request_status(
        request.id, PurchaseRequestStatus.RECIBIDA, quotes=None, warehouse_id=warehouse.id,
        location="Estante B-2",
    )

    lot = session.execute(select(PartLot).where(PartLot.part_id == part.id)).scalar_one()
    assert lot.location == "Estante B-2"

    parts = await parts_service.list_parts(filial_id)
    assert parts[0].location == "Estante B-2"


@pytest.mark.asyncio
async def test_receiving_a_purchase_request_without_a_location_leaves_the_lot_unlocated(env):
    parts_service, admin_service, session, filial_id, warehouse = env
    part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-5", name="Repuesto", price=0)
    supplier = Supplier(
        filial_id=filial_id, business_name="Proveedor Y", rif="J-87654321-0",
        supplier_type=SupplierType.IMPORTADOR, status=SupplierStatus.ACTIVO,
    )
    session.add_all([part, supplier])
    session.commit()

    request = PurchaseRequest(
        filial_id=filial_id, sequence_number=2, supplier_id=supplier.id,
        status=PurchaseRequestStatus.PAGADA,
    )
    session.add(request)
    session.flush()
    session.add(PurchaseRequestLine(purchase_request_id=request.id, part_id=part.id, quantity=10, unit_cost=5))
    session.commit()

    await admin_service.update_request_status(
        request.id, PurchaseRequestStatus.RECIBIDA, quotes=None, warehouse_id=warehouse.id,
    )

    lot = session.execute(select(PartLot).where(PartLot.part_id == part.id)).scalar_one()
    assert lot.location is None
