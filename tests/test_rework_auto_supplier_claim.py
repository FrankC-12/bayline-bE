"""When a rework claim's cause is a defective part, the system generates the
SupplierClaim on its own — tracing part -> lot -> purchase order -> supplier
via the allocations Part B now records, instead of anyone looking it up.
The auto-claim only fires when the rework claim is CLOSED with that cause —
never at creation, since the cause may not be known yet."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exceptions import BadRequestError
from app.modules.administracion.enums import PurchaseRequestStatus, SupplierStatus, SupplierType
from app.modules.administracion.models import PurchaseRequest, Supplier, SupplierClaim
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part
from app.modules.service_orders.enums import ReworkClaimStatus, ReworkFailureCategory
from app.modules.service_orders.models import ServiceOrder, ServiceOrderInvoice
from app.modules.service_orders.schemas import ReworkClaimCloseInput, ReworkClaimCreate
from app.modules.service_orders.service import ServiceOrderService
from app.modules.warehouse.models import PartLot, Warehouse


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))

        warehouse = Warehouse(filial_id=filial_id, name="Principal")
        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Alternador", price=10, stock_quantity=0)
        session.add_all([warehouse, part])

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
        session.add(
            ServiceOrderInvoice(
                service_order_id=order.id, request_id=uuid.uuid4(), request_hash="h", code=f"FAC-{order.id}",
                issued_at=datetime.now(UTC), total_usd=100, document={}, billed_client_id=client.id,
                amount_paid_at_issuance=100,
            )
        )
        session.commit()

        db = AsyncAdapter(session)
        yield ServiceOrderService(db), session, filial_id, order, part, warehouse


def make_supplier_and_po(session, filial_id, name="Importadora XYZ"):
    supplier = Supplier(
        filial_id=filial_id, business_name=name, rif="J-12345678-9",
        supplier_type=SupplierType.IMPORTADOR, status=SupplierStatus.ACTIVO,
    )
    session.add(supplier)
    session.commit()
    request = PurchaseRequest(
        filial_id=filial_id, sequence_number=1, supplier_id=supplier.id, status=PurchaseRequestStatus.RECIBIDA,
    )
    session.add(request)
    session.commit()
    return supplier, request


def make_lot(session, filial_id, warehouse, part, quantity, purchase_request_id=None, received_at=None):
    lot = PartLot(
        filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
        quantity_received=quantity, quantity_remaining=quantity, unit_cost=5,
        purchase_request_id=purchase_request_id, received_at=received_at or datetime.now(UTC),
    )
    session.add(lot)
    part.stock_quantity += quantity
    session.commit()
    return lot


@pytest.mark.asyncio
async def test_open_claim_has_no_auto_claim_info_yet(env):
    service, session, filial_id, order, part, warehouse = env
    _supplier, request = make_supplier_and_po(session, filial_id)
    make_lot(session, filial_id, warehouse, part, quantity=10, purchase_request_id=request.id)
    transfer = await service.add_transfer_line(order.id, part.id, 2)
    await service.mark_transfer_ordered(transfer.id)

    claim = await service.create_rework_claim(
        order.id,
        ReworkClaimCreate(
            failure_category=ReworkFailureCategory.REPUESTO_DEFECTUOSO,
            failure_cause="El alternador llegó defectuoso", part_id=part.id,
        ),
        None,
    )

    assert claim.status == ReworkClaimStatus.ABIERTO
    assert claim.auto_generated_supplier_claim_ids == []
    assert claim.supplier_claim_note is None
    assert session.scalar(select(SupplierClaim).where(SupplierClaim.rework_claim_id == claim.id)) is None


@pytest.mark.asyncio
async def test_cannot_close_without_ever_having_a_failure_category(env):
    service, _session, _filial_id, order, part, _warehouse = env
    claim = await service.create_rework_claim(
        order.id, ReworkClaimCreate(failure_cause="Aún sin diagnosticar"), None
    )
    assert claim.failure_category is None

    with pytest.raises(BadRequestError):
        await service.close_rework_claim(claim.id, ReworkClaimCloseInput(), None)


@pytest.mark.asyncio
async def test_cannot_close_an_already_closed_claim(env):
    service, _session, _filial_id, order, _part, _warehouse = env
    claim = await service.create_rework_claim(
        order.id,
        ReworkClaimCreate(failure_category=ReworkFailureCategory.MANO_DE_OBRA, failure_cause="Mal ajustado"),
        None,
    )
    await service.close_rework_claim(claim.id, ReworkClaimCloseInput(), None)

    with pytest.raises(BadRequestError):
        await service.close_rework_claim(claim.id, ReworkClaimCloseInput(), None)


@pytest.mark.asyncio
async def test_closing_with_a_known_po_auto_creates_the_supplier_claim(env):
    service, session, filial_id, order, part, warehouse = env
    supplier, request = make_supplier_and_po(session, filial_id)
    make_lot(session, filial_id, warehouse, part, quantity=10, purchase_request_id=request.id)

    transfer = await service.add_transfer_line(order.id, part.id, 2)
    await service.mark_transfer_ordered(transfer.id)

    claim = await service.create_rework_claim(
        order.id,
        ReworkClaimCreate(failure_cause="El alternador llegó defectuoso", part_id=part.id),
        None,
    )
    closer_id = uuid.uuid4()
    closed = await service.close_rework_claim(
        claim.id, ReworkClaimCloseInput(failure_category=ReworkFailureCategory.REPUESTO_DEFECTUOSO), closer_id
    )

    assert closed.status == ReworkClaimStatus.CERRADO
    assert closed.closed_by_user_id == closer_id
    assert closed.closed_at is not None
    assert len(closed.auto_generated_supplier_claim_ids) == 1
    assert closed.supplier_claim_note is None

    supplier_claim = session.get(SupplierClaim, closed.auto_generated_supplier_claim_ids[0])
    assert supplier_claim.part_id == part.id
    assert supplier_claim.quantity == 2
    assert supplier_claim.supplier_id == supplier.id
    assert supplier_claim.purchase_request_id == request.id
    assert supplier_claim.rework_claim_id == closed.id


@pytest.mark.asyncio
async def test_closing_from_a_lot_without_a_po_generates_no_claim(env):
    service, session, filial_id, order, part, warehouse = env
    make_lot(session, filial_id, warehouse, part, quantity=10, purchase_request_id=None)

    transfer = await service.add_transfer_line(order.id, part.id, 1)
    await service.mark_transfer_ordered(transfer.id)

    claim = await service.create_rework_claim(
        order.id,
        ReworkClaimCreate(
            failure_category=ReworkFailureCategory.REPUESTO_DEFECTUOSO,
            failure_cause="Falló temprano", part_id=part.id,
        ),
        None,
    )
    closed = await service.close_rework_claim(claim.id, ReworkClaimCloseInput(), None)

    assert closed.auto_generated_supplier_claim_ids == []
    assert closed.supplier_claim_note is not None
    assert "orden de compra" in closed.supplier_claim_note


@pytest.mark.asyncio
async def test_closing_a_never_dispatched_part_generates_no_claim(env):
    service, session, filial_id, order, part, warehouse = env
    make_lot(session, filial_id, warehouse, part, quantity=10)
    # The part is on the ODT (so the existing "belongs to this order" check
    # passes) but the ODT was never marked "Pedido" — no lot was ever
    # consumed for it, so there's nothing to trace to a supplier.
    await service.add_transfer_line(order.id, part.id, 1)

    claim = await service.create_rework_claim(
        order.id,
        ReworkClaimCreate(
            failure_category=ReworkFailureCategory.REPUESTO_DEFECTUOSO,
            failure_cause="Nunca se despachó por ODT", part_id=part.id,
        ),
        None,
    )
    closed = await service.close_rework_claim(claim.id, ReworkClaimCloseInput(), None)

    assert closed.auto_generated_supplier_claim_ids == []
    assert closed.supplier_claim_note is not None
    assert "despacho" in closed.supplier_claim_note


@pytest.mark.asyncio
async def test_multi_lot_consumption_creates_one_claim_per_supplier(env):
    service, session, filial_id, order, part, warehouse = env
    _supplier_a, request_a = make_supplier_and_po(session, filial_id, "Proveedor A")
    _supplier_b, request_b = make_supplier_and_po(session, filial_id, "Proveedor B")
    make_lot(session, filial_id, warehouse, part, quantity=2, purchase_request_id=request_a.id, received_at=datetime(2026, 1, 1, tzinfo=UTC))
    make_lot(session, filial_id, warehouse, part, quantity=10, purchase_request_id=request_b.id, received_at=datetime(2026, 2, 1, tzinfo=UTC))

    transfer = await service.add_transfer_line(order.id, part.id, 5)
    await service.mark_transfer_ordered(transfer.id)

    claim = await service.create_rework_claim(
        order.id,
        ReworkClaimCreate(
            failure_category=ReworkFailureCategory.REPUESTO_DEFECTUOSO,
            failure_cause="Defecto de fábrica", part_id=part.id,
        ),
        None,
    )
    closed = await service.close_rework_claim(claim.id, ReworkClaimCloseInput(), None)

    assert len(closed.auto_generated_supplier_claim_ids) == 2
    quantities = sorted(
        session.get(SupplierClaim, cid).quantity for cid in closed.auto_generated_supplier_claim_ids
    )
    assert quantities == [2, 3]


@pytest.mark.asyncio
async def test_non_defective_category_never_triggers_a_claim(env):
    service, session, filial_id, order, part, warehouse = env
    _supplier, request = make_supplier_and_po(session, filial_id)
    make_lot(session, filial_id, warehouse, part, quantity=10, purchase_request_id=request.id)

    transfer = await service.add_transfer_line(order.id, part.id, 1)
    await service.mark_transfer_ordered(transfer.id)

    claim = await service.create_rework_claim(
        order.id,
        ReworkClaimCreate(
            failure_category=ReworkFailureCategory.MANO_DE_OBRA,
            failure_cause="Mal ajustado", part_id=part.id,
        ),
        None,
    )
    closed = await service.close_rework_claim(claim.id, ReworkClaimCloseInput(), None)

    assert closed.auto_generated_supplier_claim_ids == []
    assert closed.supplier_claim_note is None
    assert session.scalar(select(SupplierClaim).where(SupplierClaim.rework_claim_id == closed.id)) is None
