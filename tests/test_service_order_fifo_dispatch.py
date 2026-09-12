"""Dispatching parts on a service order (ODT -> 'Pedido') now consumes real
FIFO lots — oldest first, across every warehouse in the filial — instead of
just decrementing Part.stock_quantity. This is what lets a rework claim
later trace a part back to the lot, the purchase order, and the supplier
(F0-01)."""

import uuid
from datetime import UTC, datetime

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
from app.modules.service_orders.models import ServiceOrder, ServiceOrderTransferLotAllocation
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
        part = Part(filial_id=filial_id, code="P-1", name="Alternador", price=10, stock_quantity=0)
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

    transfer = await service.add_transfer_line(order.id, part.id, 3)
    await service.mark_transfer_ordered(transfer.id)

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
async def test_add_line_with_insufficient_lot_stock_raises(env):
    service, session, filial_id, order, part, warehouse, _other = env
    make_lot(session, filial_id, warehouse, part, quantity=1)

    with pytest.raises(InsufficientStockError):
        await service.add_transfer_line(order.id, part.id, 5)


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

    admin = AdministracionService(AsyncAdapter(session))
    await admin.update_request_status(
        request.id, PurchaseRequestStatus.COTIZADA, [QuoteLineInput(line_id=line.id, unit_cost=5)], None
    )
    await admin.update_request_status(request.id, PurchaseRequestStatus.PAGADA, None, None)
    await admin.update_request_status(request.id, PurchaseRequestStatus.RECIBIDA, None, warehouse.id)

    lot = session.scalar(select(PartLot).where(PartLot.part_id == part.id))
    assert lot is not None
    assert lot.purchase_request_id == request.id
