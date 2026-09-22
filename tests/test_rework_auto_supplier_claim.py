"""When a warranty claim's cause is a defective part (claim_type=
repuesto_proveedor), the system generates the SupplierClaim on its own —
tracing part -> lot -> purchase order -> supplier via the allocations Part B
now records, instead of anyone looking it up. The auto-claim only fires when
the claim is CONVERTED to an order — never at creation or authorization,
since the cause may not be known yet at creation and converting is the step
that actually commits the shop to the rework."""

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
from app.modules.service_orders.enums import (
    ReworkFailureCategory,
    ServiceOrderPayer,
    WarrantyClaimStatus,
    WarrantyClaimType,
)
from app.modules.service_orders.models import ServiceOrder, ServiceOrderInvoice
from app.modules.service_orders.schemas import WarrantyClaimAuthorizationInput, WarrantyClaimConvertInput, WarrantyClaimCreate
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


def claim_payload(order, part, **overrides):
    defaults = dict(
        claim_type=WarrantyClaimType.REPUESTO_PROVEEDOR,
        vehicle_id=order.vehicle_id,
        service_order_id=order.id,
        part_id=part.id,
        failure_cause="El alternador llegó defectuoso",
        reported_mileage=0,
    )
    defaults.update(overrides)
    return WarrantyClaimCreate(**defaults)


async def _authorize_and_convert(service, claim_id, user_id=None, failure_category=None):
    await service.authorize_warranty_claim(
        claim_id, WarrantyClaimAuthorizationInput(decision="aprobado", failure_category=failure_category), user_id
    )
    return await service.convert_warranty_claim_to_order(claim_id, WarrantyClaimConvertInput(), user_id)


@pytest.mark.asyncio
async def test_open_claim_has_no_auto_claim_info_yet(env):
    service, session, filial_id, order, part, warehouse = env
    _supplier, request = make_supplier_and_po(session, filial_id)
    make_lot(session, filial_id, warehouse, part, quantity=10, purchase_request_id=request.id)
    transfer = await service.add_transfer_line(order.id, part.id, 2)
    await service.mark_transfer_ordered(transfer.id)

    claim = await service.create_warranty_claim(claim_payload(order, part), [], [], None)

    assert claim.status == WarrantyClaimStatus.SOLICITADO
    assert claim.auto_generated_supplier_claim_ids == []
    assert claim.supplier_claim_note is None
    assert session.scalar(select(SupplierClaim).where(SupplierClaim.warranty_claim_id == claim.id)) is None


@pytest.mark.asyncio
async def test_cannot_authorize_without_ever_having_a_failure_category(env):
    service, _session, _filial_id, order, part, _warehouse = env
    claim = await service.create_warranty_claim(
        claim_payload(order, part, part_id=None, failure_cause="Aún sin diagnosticar"), [], [], None
    )
    assert claim.failure_category is None

    with pytest.raises(BadRequestError):
        await service.authorize_warranty_claim(
            claim.id, WarrantyClaimAuthorizationInput(decision="aprobado"), None
        )


@pytest.mark.asyncio
async def test_cannot_authorize_an_already_decided_claim(env):
    service, _session, _filial_id, order, part, _warehouse = env
    claim = await service.create_warranty_claim(
        claim_payload(order, part, part_id=None, failure_cause="Mal ajustado"), [], [], None
    )
    await service.authorize_warranty_claim(
        claim.id, WarrantyClaimAuthorizationInput(decision="aprobado", failure_category=ReworkFailureCategory.MANO_DE_OBRA), None
    )

    with pytest.raises(BadRequestError):
        await service.authorize_warranty_claim(
            claim.id, WarrantyClaimAuthorizationInput(decision="aprobado", failure_category=ReworkFailureCategory.MANO_DE_OBRA), None
        )


@pytest.mark.asyncio
async def test_converting_with_a_known_po_auto_creates_the_supplier_claim(env):
    service, session, filial_id, order, part, warehouse = env
    supplier, request = make_supplier_and_po(session, filial_id)
    make_lot(session, filial_id, warehouse, part, quantity=10, purchase_request_id=request.id)

    transfer = await service.add_transfer_line(order.id, part.id, 2)
    await service.mark_transfer_ordered(transfer.id)

    claim = await service.create_warranty_claim(claim_payload(order, part), [], [], None)
    converter_id = uuid.uuid4()
    converted = await _authorize_and_convert(
        service, claim.id, converter_id, failure_category=ReworkFailureCategory.REPUESTO_DEFECTUOSO
    )

    assert converted.status == WarrantyClaimStatus.CONVERTIDO_A_ODS
    assert converted.converted_by_user_id == converter_id
    assert converted.converted_at is not None
    assert len(converted.auto_generated_supplier_claim_ids) == 1
    assert converted.supplier_claim_note is None

    supplier_claim = session.get(SupplierClaim, converted.auto_generated_supplier_claim_ids[0])
    assert supplier_claim.part_id == part.id
    assert supplier_claim.quantity == 2
    assert supplier_claim.supplier_id == supplier.id
    assert supplier_claim.purchase_request_id == request.id
    assert supplier_claim.warranty_claim_id == converted.id


@pytest.mark.asyncio
async def test_converting_still_tags_the_new_line_as_proveedor_despite_the_manual_block(env):
    """A human can no longer pick 'proveedor' by hand in TransferLineInput/
    TaskCreate (see test_service_order_payer.py), but this conversion sets it
    by calling add_transfer_line/add_task directly, bypassing those schemas —
    so it must keep working unchanged."""
    service, session, filial_id, order, part, warehouse = env
    make_supplier_and_po(session, filial_id)
    make_lot(session, filial_id, warehouse, part, quantity=10, purchase_request_id=None)

    transfer = await service.add_transfer_line(order.id, part.id, 2)
    await service.mark_transfer_ordered(transfer.id)

    claim = await service.create_warranty_claim(claim_payload(order, part), [], [], None)
    converted = await _authorize_and_convert(
        service, claim.id, failure_category=ReworkFailureCategory.REPUESTO_DEFECTUOSO
    )

    new_transfers = await service.list_transfers(converted.resulting_service_order_id)
    new_lines = [line for tr in new_transfers for line in tr.lines]
    assert len(new_lines) == 1
    assert new_lines[0].payer == ServiceOrderPayer.PROVEEDOR


@pytest.mark.asyncio
async def test_converting_from_a_lot_without_a_po_generates_no_claim(env):
    service, session, filial_id, order, part, warehouse = env
    make_lot(session, filial_id, warehouse, part, quantity=10, purchase_request_id=None)

    transfer = await service.add_transfer_line(order.id, part.id, 1)
    await service.mark_transfer_ordered(transfer.id)

    claim = await service.create_warranty_claim(
        claim_payload(order, part, failure_cause="Falló temprano"), [], [], None
    )
    converted = await _authorize_and_convert(
        service, claim.id, failure_category=ReworkFailureCategory.REPUESTO_DEFECTUOSO
    )

    assert converted.auto_generated_supplier_claim_ids == []
    assert converted.supplier_claim_note is not None
    assert "orden de compra" in converted.supplier_claim_note


@pytest.mark.asyncio
async def test_converting_a_never_dispatched_part_generates_no_claim(env):
    service, session, filial_id, order, part, warehouse = env
    make_lot(session, filial_id, warehouse, part, quantity=10)
    # The part is on the ODT (so the existing "belongs to this order" check
    # passes) but the ODT was never marked "Pedido" — no lot was ever
    # consumed for it, so there's nothing to trace to a supplier.
    await service.add_transfer_line(order.id, part.id, 1)

    claim = await service.create_warranty_claim(
        claim_payload(order, part, failure_cause="Nunca se despachó por ODT"), [], [], None
    )
    converted = await _authorize_and_convert(
        service, claim.id, failure_category=ReworkFailureCategory.REPUESTO_DEFECTUOSO
    )

    assert converted.auto_generated_supplier_claim_ids == []
    assert converted.supplier_claim_note is not None
    assert "despacho" in converted.supplier_claim_note


@pytest.mark.asyncio
async def test_multi_lot_consumption_creates_one_claim_per_supplier(env):
    service, session, filial_id, order, part, warehouse = env
    _supplier_a, request_a = make_supplier_and_po(session, filial_id, "Proveedor A")
    _supplier_b, request_b = make_supplier_and_po(session, filial_id, "Proveedor B")
    make_lot(session, filial_id, warehouse, part, quantity=2, purchase_request_id=request_a.id, received_at=datetime(2026, 1, 1, tzinfo=UTC))
    make_lot(session, filial_id, warehouse, part, quantity=10, purchase_request_id=request_b.id, received_at=datetime(2026, 2, 1, tzinfo=UTC))

    transfer = await service.add_transfer_line(order.id, part.id, 5)
    await service.mark_transfer_ordered(transfer.id)

    claim = await service.create_warranty_claim(
        claim_payload(order, part, failure_cause="Defecto de fábrica"), [], [], None
    )
    converted = await _authorize_and_convert(
        service, claim.id, failure_category=ReworkFailureCategory.REPUESTO_DEFECTUOSO
    )

    assert len(converted.auto_generated_supplier_claim_ids) == 2
    quantities = sorted(
        session.get(SupplierClaim, cid).quantity for cid in converted.auto_generated_supplier_claim_ids
    )
    assert quantities == [2, 3]


@pytest.mark.asyncio
async def test_comeback_claim_type_never_triggers_a_supplier_claim(env):
    """The auto-claim is gated by claim_type == repuesto_proveedor, not by
    failure_category — a comeback (garantía taller assumes it) never
    generates one even with a part_id and a defective-part-sounding cause."""
    service, session, filial_id, order, part, warehouse = env
    _supplier, request = make_supplier_and_po(session, filial_id)
    make_lot(session, filial_id, warehouse, part, quantity=10, purchase_request_id=request.id)

    transfer = await service.add_transfer_line(order.id, part.id, 1)
    await service.mark_transfer_ordered(transfer.id)

    claim = await service.create_warranty_claim(
        claim_payload(order, part, claim_type=WarrantyClaimType.COMEBACK, failure_cause="Mal ajustado"), [], [], None
    )
    converted = await _authorize_and_convert(
        service, claim.id, failure_category=ReworkFailureCategory.MANO_DE_OBRA
    )

    assert converted.auto_generated_supplier_claim_ids == []
    assert converted.supplier_claim_note is None
    assert session.scalar(select(SupplierClaim).where(SupplierClaim.warranty_claim_id == converted.id)) is None
