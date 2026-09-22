"""Dispatching parts on a service order (ODT -> 'Pedido') now consumes real
FIFO lots — oldest first, across every warehouse in the filial — instead of
just decrementing Part.stock_quantity. This is what lets a rework claim
later trace a part back to the lot, the purchase order, and the supplier
(F0-01)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.administracion.enums import PurchaseRequestStatus, SupplierStatus, SupplierType
from app.modules.administracion.models import PurchaseRequest, Supplier
from app.modules.administracion.schemas import QuoteLineInput
from app.modules.administracion.service import AdministracionService
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part
from app.modules.service_orders.models import (
    ServiceOrder,
    ServiceOrderTransferLine,
    ServiceOrderTransferLotAllocation,
)
from app.modules.service_orders.service import ServiceOrderService
from app.modules.warehouse.exceptions import InsufficientStockError
from app.modules.warehouse.models import PartLot, StockMovement, Warehouse


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))

        warehouse = Warehouse(filial_id=filial_id, name="Principal")
        other_warehouse = Warehouse(filial_id=filial_id, name="Otro")
        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Alternador", price=10, stock_quantity=0)
        session.add_all([warehouse, other_warehouse, part])

        client = Client(
            filial_id=filial_id, full_name="Cliente", client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V, document_number="12345678", phone_primary="04121234567", address="Caracas",
        )
        session.add(client)
        session.commit()
        vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Corolla", plate="ABC123")
        session.add(vehicle)
        session.commit()

        order = ServiceOrder(filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id)
        session.add(order)
        session.commit()

        db = AsyncAdapter(session)
        yield ServiceOrderService(db), session, filial_id, order, part, warehouse, other_warehouse


def make_lot(session, filial_id, warehouse, part, quantity, unit_cost=5, purchase_request_id=None, received_at=None):
    lot = PartLot(
        filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
        quantity_received=quantity, quantity_remaining=quantity, unit_cost=unit_cost,
        purchase_request_id=purchase_request_id,
        received_at=received_at or datetime.now(UTC),
    )
    session.add(lot)
    part.stock_quantity += quantity
    session.commit()
    return lot


@pytest.mark.asyncio
async def test_dispatch_consumes_a_single_lot_and_traces_it(env):
    service, session, filial_id, order, part, warehouse, _other = env
    lot = make_lot(session, filial_id, warehouse, part, quantity=10)

    almacenista_id = uuid.uuid4()
    transfer = await service.add_transfer_line(order.id, part.id, 3)
    await service.mark_transfer_ordered(transfer.id, almacenista_id)

    session.refresh(lot)
    assert lot.quantity_remaining == 7
    session.refresh(part)
    assert part.stock_quantity == 7

    allocations = session.scalars(select(ServiceOrderTransferLotAllocation)).all()
    assert len(allocations) == 1
    assert allocations[0].lot_id == lot.id
    assert allocations[0].quantity == 3

    movements = session.scalars(select(StockMovement)).all()
    assert len(movements) == 1
    assert movements[0].quantity == 3
    assert movements[0].warehouse_id == warehouse.id
    assert movements[0].responsible_user_id == almacenista_id


@pytest.mark.asyncio
async def test_a_fresh_reservation_blocks_another_line_from_the_same_lot(env):
    """Two different ODTs request the same part when only one cheap layer
    has enough stock for one of them — the first to preview it reserves
    those units, so the second (previewed right after) must fall to the
    pricier layer instead of pricing itself off units that are about to be
    dispatched out from under it."""
    service, session, filial_id, order, part, warehouse, _other = env
    make_lot(session, filial_id, warehouse, part, quantity=2, unit_cost=10, received_at=datetime(2026, 1, 1, tzinfo=UTC))
    make_lot(session, filial_id, warehouse, part, quantity=10, unit_cost=20, received_at=datetime(2026, 2, 1, tzinfo=UTC))

    other_order = ServiceOrder(filial_id=filial_id, sequence_number=2, vehicle_id=order.vehicle_id)
    session.add(other_order)
    session.commit()

    first_transfer = await service.add_transfer_line(order.id, part.id, 2)
    second_transfer = await service.add_transfer_line(other_order.id, part.id, 2)

    first_line = first_transfer.lines[0]
    second_line = second_transfer.lines[0]
    assert float(first_line.cost_total) == 20.0  # 2 units @ $10 (the cheap layer)
    assert float(second_line.cost_total) == 40.0  # 2 units @ $20 — cheap layer already reserved


@pytest.mark.asyncio
async def test_a_lapsed_reservation_no_longer_blocks_another_line(env):
    """Past RESERVATION_TTL, a reservation stops counting on its own — no
    active cleanup needed — so a second line can then claim those units."""
    service, session, filial_id, order, part, warehouse, _other = env
    make_lot(session, filial_id, warehouse, part, quantity=2, unit_cost=10, received_at=datetime(2026, 1, 1, tzinfo=UTC))
    make_lot(session, filial_id, warehouse, part, quantity=10, unit_cost=20, received_at=datetime(2026, 2, 1, tzinfo=UTC))

    other_order = ServiceOrder(filial_id=filial_id, sequence_number=2, vehicle_id=order.vehicle_id)
    session.add(other_order)
    session.commit()

    first_transfer = await service.add_transfer_line(order.id, part.id, 2)
    stale_allocation = session.scalars(
        select(ServiceOrderTransferLotAllocation).where(
            ServiceOrderTransferLotAllocation.transfer_line_id == first_transfer.lines[0].id
        )
    ).one()
    stale_allocation.created_at = datetime.now(UTC) - timedelta(minutes=6)
    session.commit()

    second_transfer = await service.add_transfer_line(other_order.id, part.id, 2)

    assert float(second_transfer.lines[0].cost_total) == 20.0  # cheap layer, reservation lapsed


@pytest.mark.asyncio
async def test_dispatch_within_the_reservation_window_matches_the_previewed_price(env):
    """Dispatching before the reservation lapses must land on the exact
    same lots/price shown while the ODT was Pendiente — the whole point of
    reserving them."""
    service, session, filial_id, order, part, warehouse, _other = env
    make_lot(session, filial_id, warehouse, part, quantity=2, unit_cost=10, received_at=datetime(2026, 1, 1, tzinfo=UTC))
    make_lot(session, filial_id, warehouse, part, quantity=10, unit_cost=20, received_at=datetime(2026, 2, 1, tzinfo=UTC))

    transfer = await service.add_transfer_line(order.id, part.id, 3)
    previewed_line = transfer.lines[0]
    previewed_cost, previewed_unit_price = float(previewed_line.cost_total), float(previewed_line.unit_price)

    dispatched = await service.mark_transfer_ordered(transfer.id)

    dispatched_line = dispatched.lines[0]
    assert float(dispatched_line.cost_total) == previewed_cost == 40.0  # 2 @ $10 + 1 @ $20
    assert float(dispatched_line.unit_price) == pytest.approx(previewed_unit_price)


@pytest.mark.asyncio
async def test_dispatch_multiple_parts_on_one_odt_including_exact_full_consumption(env):
    """Mirrors the reported scenario exactly: one ODT with two parts —
    dispatching all 5 remaining units of one part (5 -> 0) must not be
    blocked as if it were an overdraft, and a second part on the same ODT
    (7 -> 6) must dispatch correctly too, each leaving its own StockMovement."""
    service, session, filial_id, order, oil, warehouse, _other = env
    oil.name = "Aceite 15W40"
    filter_part = Part(
        category_id=uuid.uuid4(), filial_id=filial_id, code="P-2", name="Filtro", price=5, stock_quantity=0
    )
    session.add(filter_part)
    session.commit()
    oil_lot = make_lot(session, filial_id, warehouse, oil, quantity=5)
    filter_lot = make_lot(session, filial_id, warehouse, filter_part, quantity=7)

    transfer = await service.add_transfer_line(order.id, oil.id, 5)
    await service.add_transfer_line(order.id, filter_part.id, 1)
    await service.mark_transfer_ordered(transfer.id)

    session.refresh(oil_lot)
    session.refresh(filter_lot)
    session.refresh(oil)
    session.refresh(filter_part)
    assert oil_lot.quantity_remaining == 0
    assert oil.stock_quantity == 0
    assert filter_lot.quantity_remaining == 6
    assert filter_part.stock_quantity == 6

    movements = {m.part_id: m for m in session.scalars(select(StockMovement)).all()}
    assert movements[oil.id].quantity == 5
    assert movements[filter_part.id].quantity == 1


@pytest.mark.asyncio
async def test_dispatch_splits_across_lots_oldest_first(env):
    service, session, filial_id, order, part, warehouse, other_warehouse = env
    older = make_lot(session, filial_id, warehouse, part, quantity=2, unit_cost=4, received_at=datetime(2026, 1, 1, tzinfo=UTC))
    newer = make_lot(session, filial_id, other_warehouse, part, quantity=10, unit_cost=6, received_at=datetime(2026, 2, 1, tzinfo=UTC))

    transfer = await service.add_transfer_line(order.id, part.id, 5)
    await service.mark_transfer_ordered(transfer.id)

    session.refresh(older)
    session.refresh(newer)
    assert older.quantity_remaining == 0
    assert newer.quantity_remaining == 7

    allocations = session.scalars(select(ServiceOrderTransferLotAllocation)).all()
    by_lot = {a.lot_id: a.quantity for a in allocations}
    assert by_lot[older.id] == 2
    assert by_lot[newer.id] == 3


@pytest.mark.asyncio
async def test_add_line_with_insufficient_lot_stock_warns_but_still_creates_the_line(env):
    """Adding a line is never blocked by stock — a client may bring their own
    part, or it may get requested from another branch later. Only dispatch
    ("pedir a almacén", mark_transfer_ordered) actually blocks on stock."""
    service, session, filial_id, order, part, warehouse, _other = env
    make_lot(session, filial_id, warehouse, part, quantity=1)

    transfer = await service.add_transfer_line(order.id, part.id, 5)

    assert len(transfer.stock_warnings) == 1
    assert "insuficiente" in transfer.stock_warnings[0].lower()
    line = session.scalars(
        select(ServiceOrderTransferLine).where(ServiceOrderTransferLine.transfer_id == transfer.id)
    ).one()
    assert line.quantity == 5

    with pytest.raises(InsufficientStockError) as excinfo:
        await service.mark_transfer_ordered(transfer.id)

    # A dispatch failure must name which part ran short — an ODT can have
    # several lines, and a generic "insufficient stock" toast leaves the
    # user unable to tell which one to fix.
    assert "Alternador" in excinfo.value.message
    assert excinfo.value.details == [{"field": str(part.id), "message": excinfo.value.message}]


@pytest.mark.asyncio
async def test_dispatch_revalidates_stock_if_it_shrank_since_the_line_was_added(env):
    service, session, filial_id, order, part, warehouse, _other = env
    lot = make_lot(session, filial_id, warehouse, part, quantity=5)

    # Enough stock existed when the line was priced/added...
    transfer = await service.add_transfer_line(order.id, part.id, 5)
    # ...but another order consumed most of it before this one dispatched.
    lot.quantity_remaining = 1
    session.commit()

    with pytest.raises(InsufficientStockError):
        await service.mark_transfer_ordered(transfer.id)


@pytest.mark.asyncio
async def test_receiving_a_purchase_request_stamps_its_id_on_the_new_lot(env):
    _service, session, filial_id, _order, part, warehouse, _other = env
    supplier = Supplier(
        filial_id=filial_id, business_name="Importadora XYZ", rif="J-12345678-9",
        supplier_type=SupplierType.IMPORTADOR, status=SupplierStatus.ACTIVO,
    )
    session.add(supplier)
    session.commit()

    request = PurchaseRequest(
        filial_id=filial_id, sequence_number=1, supplier_id=supplier.id, status=PurchaseRequestStatus.ENVIADA,
    )
    session.add(request)
    session.commit()
    from app.modules.administracion.models import PurchaseRequestLine

    line = PurchaseRequestLine(purchase_request_id=request.id, part_id=part.id, quantity=4, unit_cost=None)
    session.add(line)
    session.commit()

    comprador_id = uuid.uuid4()
    admin = AdministracionService(AsyncAdapter(session))
    await admin.update_request_status(
        request.id, PurchaseRequestStatus.COTIZADA, [QuoteLineInput(line_id=line.id, unit_cost=5)], None
    )
    await admin.update_request_status(request.id, PurchaseRequestStatus.PAGADA, None, None)
    await admin.update_request_status(
        request.id, PurchaseRequestStatus.RECIBIDA, None, warehouse.id, responsible_user_id=comprador_id
    )

    lot = session.scalar(select(PartLot).where(PartLot.part_id == part.id))
    assert lot is not None
    assert lot.purchase_request_id == request.id

    movement = session.scalar(select(StockMovement).where(StockMovement.part_id == part.id))
    assert movement is not None
    assert movement.responsible_user_id == comprador_id
