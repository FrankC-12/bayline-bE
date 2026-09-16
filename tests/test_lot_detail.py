"""A lot's detail view unifies every dispatched consumption of that exact
lot — a counter sale (Venta de Repuestos) or a workshop ODT — into one
chronological "salidas" list. Preview/pending ODT allocations (a line added
but not yet dispatched) must never show up as if they were real consumption.
Searching lots by code ("L-104") matches the lot_number, independent of a
part filter."""

import os
import uuid
from datetime import UTC, datetime, timedelta

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part, PartSale, PartSaleLine, PartSaleLotAllocation
from app.modules.service_orders.enums import TransferStatus
from app.modules.service_orders.models import (
    ServiceOrder,
    ServiceOrderTransfer,
    ServiceOrderTransferLine,
    ServiceOrderTransferLotAllocation,
)
from app.modules.warehouse.exceptions import PartLotNotFoundError
from app.modules.warehouse.models import PartLot, Warehouse
from app.modules.warehouse.service import AlmacenService


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        warehouse = Warehouse(filial_id=filial_id, name="Almacén 1")
        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="QA-002", name="Aceite 15W40", price=0)
        session.add_all([warehouse, part])
        session.commit()
        lot = PartLot(
            filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id, lot_number=104,
            quantity_received=5, quantity_remaining=5, unit_cost=10,
        )
        session.add(lot)
        session.commit()
        yield AlmacenService(AsyncAdapter(session)), session, filial_id, warehouse, part, lot


def _make_client_vehicle(session, filial_id):
    client = Client(
        filial_id=filial_id, full_name="Cliente", client_type=ClientType.PARTICULAR,
        document_type=DocumentType.V, document_number="12345678", phone_primary="04121234567", address="Caracas",
    )
    session.add(client)
    session.commit()
    vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Corolla", plate="ABC123")
    session.add(vehicle)
    session.commit()
    return vehicle


@pytest.mark.asyncio
async def test_lot_with_no_movements(env):
    service, _session, _filial_id, _warehouse, _part, lot = env

    detail = await service.get_lot_detail(lot)

    assert detail.code == "L-104"
    assert detail.outbound_movements == []


@pytest.mark.asyncio
async def test_counter_sale_movement_is_included(env):
    service, session, filial_id, _warehouse, part, lot = env
    sale = PartSale(filial_id=filial_id, sequence_number=1, client_name="Juan Pérez")
    session.add(sale)
    session.flush()
    line = PartSaleLine(
        part_sale_id=sale.id, part_id=part.id, quantity=2, unit_price=13, unit_cost=10, line_total=26,
    )
    session.add(line)
    session.flush()
    session.add(PartSaleLotAllocation(part_sale_line_id=line.id, lot_id=lot.id, quantity=2, unit_cost=10))
    session.commit()

    detail = await service.get_lot_detail(lot)

    assert len(detail.outbound_movements) == 1
    movement = detail.outbound_movements[0]
    assert movement.source == "venta_repuestos"
    assert movement.quantity == 2
    assert movement.reference_code == "VR-1"
    assert "Juan Pérez" in movement.description
    assert movement.link_id == sale.id


@pytest.mark.asyncio
async def test_dispatched_odt_movement_is_included(env):
    service, session, filial_id, _warehouse, part, lot = env
    vehicle = _make_client_vehicle(session, filial_id)
    order = ServiceOrder(filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id)
    session.add(order)
    session.commit()
    transfer = ServiceOrderTransfer(
        service_order_id=order.id, sequence_number=1, status=TransferStatus.PEDIDO,
        fulfilled_at=datetime.now(UTC),
    )
    session.add(transfer)
    session.flush()
    line = ServiceOrderTransferLine(transfer_id=transfer.id, part_id=part.id, quantity=1)
    session.add(line)
    session.flush()
    session.add(
        ServiceOrderTransferLotAllocation(
            transfer_line_id=line.id, lot_id=lot.id, warehouse_id=_warehouse.id, quantity=1, unit_cost=10,
        )
    )
    session.commit()

    detail = await service.get_lot_detail(lot)

    assert len(detail.outbound_movements) == 1
    movement = detail.outbound_movements[0]
    assert movement.source == "odt_taller"
    assert movement.quantity == 1
    assert order.code in movement.reference_code
    assert movement.link_id == order.id


@pytest.mark.asyncio
async def test_pending_odt_allocation_is_excluded(env):
    """A line added to an ODT but not yet dispatched ("pedido a almacén")
    only carries a preview allocation — it must not appear as a real salida."""
    service, session, filial_id, warehouse, part, lot = env
    vehicle = _make_client_vehicle(session, filial_id)
    order = ServiceOrder(filial_id=filial_id, sequence_number=2, vehicle_id=vehicle.id)
    session.add(order)
    session.commit()
    transfer = ServiceOrderTransfer(
        service_order_id=order.id, sequence_number=1, status=TransferStatus.PENDIENTE,
    )
    session.add(transfer)
    session.flush()
    line = ServiceOrderTransferLine(transfer_id=transfer.id, part_id=part.id, quantity=1)
    session.add(line)
    session.flush()
    session.add(
        ServiceOrderTransferLotAllocation(
            transfer_line_id=line.id, lot_id=lot.id, warehouse_id=warehouse.id, quantity=1, unit_cost=10,
        )
    )
    session.commit()

    detail = await service.get_lot_detail(lot)

    assert detail.outbound_movements == []


@pytest.mark.asyncio
async def test_movements_from_both_sources_are_merged_newest_first(env):
    service, session, filial_id, warehouse, part, lot = env
    sale = PartSale(filial_id=filial_id, sequence_number=1, client_name="Cliente A")
    session.add(sale)
    session.flush()
    old_line = PartSaleLine(part_sale_id=sale.id, part_id=part.id, quantity=1, unit_price=13, unit_cost=10, line_total=13)
    session.add(old_line)
    session.flush()
    session.add(PartSaleLotAllocation(part_sale_line_id=old_line.id, lot_id=lot.id, quantity=1, unit_cost=10))
    session.commit()
    sale.created_at = datetime.now(UTC) - timedelta(days=5)
    session.commit()

    vehicle = _make_client_vehicle(session, filial_id)
    order = ServiceOrder(filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id)
    session.add(order)
    session.commit()
    transfer = ServiceOrderTransfer(
        service_order_id=order.id, sequence_number=1, status=TransferStatus.PEDIDO,
        fulfilled_at=datetime.now(UTC),
    )
    session.add(transfer)
    session.flush()
    odt_line = ServiceOrderTransferLine(transfer_id=transfer.id, part_id=part.id, quantity=1)
    session.add(odt_line)
    session.flush()
    session.add(
        ServiceOrderTransferLotAllocation(
            transfer_line_id=odt_line.id, lot_id=lot.id, warehouse_id=warehouse.id, quantity=1, unit_cost=10,
        )
    )
    session.commit()

    detail = await service.get_lot_detail(lot)

    assert [m.source for m in detail.outbound_movements] == ["odt_taller", "venta_repuestos"]


@pytest.mark.asyncio
async def test_get_lot_raises_when_not_found(env):
    service, _session, _filial_id, _warehouse, _part, _lot = env

    with pytest.raises(PartLotNotFoundError):
        await service.get_lot(uuid.uuid4())


@pytest.mark.asyncio
async def test_list_lots_by_code_matches_regardless_of_format(env):
    service, _session, filial_id, _warehouse, _part, lot = env

    for term in ["L-104", "l104", "104"]:
        results = await service.list_lots(filial_id, search=term)
        assert [r.id for r in results] == [lot.id], f"search={term!r}"


@pytest.mark.asyncio
async def test_list_lots_by_code_with_no_numeric_match_returns_empty(env):
    service, _session, filial_id, _warehouse, _part, _lot = env

    assert await service.list_lots(filial_id, search="aceite") == []


@pytest.mark.asyncio
async def test_list_lots_by_code_scoped_to_filial(env):
    service, _session, _filial_id, _warehouse, _part, _lot = env

    assert await service.list_lots(uuid.uuid4(), search="L-104") == []
