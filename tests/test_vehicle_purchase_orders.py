"""Vehicle purchase orders (OC) — bought by spec (brand/model/version/year/
color/quantity), received across one or more deliveries, each unit
individually identified by VIN at reception. Cost is unknown at reception
(cost_is_estimated=True) and only fixed once Administración registers the
supplier invoice linked to the OC, which splits its total across the units
it covers. Margin at sale time is specific-identification (DealershipVehicle.
cost_price), never FIFO — confirmed unaffected by this module."""

import os
import uuid
from datetime import date

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.administracion.enums import SupplierStatus, SupplierType
from app.modules.administracion.models import Supplier
from app.modules.compras.enums import VehiclePurchaseOrderStatus
from app.modules.compras.exceptions import (
    DuplicateVehicleVinError,
    InvalidVehiclePurchaseOrderStatusTransitionError,
    NoUnitsToInvoiceError,
    VehiclePurchaseOrderLineOverReceivedError,
)
from app.modules.compras.schemas import (
    ReceptionCreate,
    ReceptionUnitInput,
    VehiclePurchaseOrderCreate,
    VehiclePurchaseOrderInvoiceCreate,
    VehiclePurchaseOrderLineInput,
)
from app.modules.compras.service import ComprasService
from app.modules.concesionario.models import DealershipVehicle
from app.modules.filiales.models import Filial


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        supplier = Supplier(
            filial_id=filial_id, business_name="Toyota de Venezuela", rif="J-11111111-1",
            supplier_type=SupplierType.IMPORTADOR, status=SupplierStatus.ACTIVO,
        )
        session.add(supplier)
        session.commit()
        yield ComprasService(AsyncAdapter(session)), session, filial_id, supplier.id


def _order_payload(filial_id, supplier_id, quantity=3):
    return VehiclePurchaseOrderCreate(
        filial_id=filial_id,
        supplier_id=supplier_id,
        lines=[
            VehiclePurchaseOrderLineInput(
                brand="Toyota", model="Corolla", version="LE", year=2026, color="Blanco", quantity=quantity
            )
        ],
    )


@pytest.mark.asyncio
async def test_creating_an_order_starts_enviada_with_zero_received(env):
    service, _session, filial_id, supplier_id = env
    order = await service.create_vehicle_purchase_order(_order_payload(filial_id, supplier_id), None)

    assert order.status == VehiclePurchaseOrderStatus.ENVIADA
    assert order.lines[0].quantity_received == 0
    assert order.code.startswith("OC-")


@pytest.mark.asyncio
async def test_partial_reception_moves_status_and_tracks_progress(env):
    service, _session, filial_id, supplier_id = env
    order = await service.create_vehicle_purchase_order(_order_payload(filial_id, supplier_id), None)
    line_id = order.lines[0].id

    updated = await service.add_reception(
        order.id,
        ReceptionCreate(units=[
            ReceptionUnitInput(purchase_order_line_id=line_id, vin="1HGCM82633A000001"),
            ReceptionUnitInput(purchase_order_line_id=line_id, vin="1HGCM82633A000002"),
        ]),
        None,
    )

    assert updated.status == VehiclePurchaseOrderStatus.PARCIALMENTE_RECIBIDA
    assert updated.lines[0].quantity_received == 2
    assert len(updated.receptions) == 1
    assert all(u.cost_price is None and u.cost_is_estimated for u in updated.receptions[0].units)


@pytest.mark.asyncio
async def test_receiving_the_rest_completes_the_line(env):
    service, _session, filial_id, supplier_id = env
    order = await service.create_vehicle_purchase_order(_order_payload(filial_id, supplier_id), None)
    line_id = order.lines[0].id
    await service.add_reception(
        order.id,
        ReceptionCreate(units=[ReceptionUnitInput(purchase_order_line_id=line_id, vin="1HGCM82633A000001")]),
        None,
    )

    updated = await service.add_reception(
        order.id,
        ReceptionCreate(units=[
            ReceptionUnitInput(purchase_order_line_id=line_id, vin="1HGCM82633A000002"),
            ReceptionUnitInput(purchase_order_line_id=line_id, vin="1HGCM82633A000003"),
        ]),
        None,
    )

    assert updated.status == VehiclePurchaseOrderStatus.RECIBIDA
    assert updated.lines[0].quantity_received == 3
    assert len(updated.receptions) == 2


@pytest.mark.asyncio
async def test_receiving_past_the_ordered_quantity_is_rejected(env):
    service, _session, filial_id, supplier_id = env
    order = await service.create_vehicle_purchase_order(_order_payload(filial_id, supplier_id, quantity=1), None)
    line_id = order.lines[0].id

    with pytest.raises(VehiclePurchaseOrderLineOverReceivedError):
        await service.add_reception(
            order.id,
            ReceptionCreate(units=[
                ReceptionUnitInput(purchase_order_line_id=line_id, vin="1HGCM82633A000001"),
                ReceptionUnitInput(purchase_order_line_id=line_id, vin="1HGCM82633A000002"),
            ]),
            None,
        )


@pytest.mark.asyncio
async def test_a_vin_already_used_by_another_vehicle_is_rejected(env):
    service, session, filial_id, supplier_id = env
    session.add(
        DealershipVehicle(
            filial_id=filial_id, status="disponible", condition="nuevo", brand="Toyota", model="Yaris",
            year=2025, vin="1HGCM82633A000001", sku="EXIST-1", price_cash=10000, price_financed=11000,
        )
    )
    session.commit()
    order = await service.create_vehicle_purchase_order(_order_payload(filial_id, supplier_id), None)
    line_id = order.lines[0].id

    with pytest.raises(DuplicateVehicleVinError):
        await service.add_reception(
            order.id,
            ReceptionCreate(units=[ReceptionUnitInput(purchase_order_line_id=line_id, vin="1HGCM82633A000001")]),
            None,
        )


@pytest.mark.asyncio
async def test_invoice_splits_amount_evenly_and_fixes_real_cost(env):
    service, session, filial_id, supplier_id = env
    order = await service.create_vehicle_purchase_order(_order_payload(filial_id, supplier_id, quantity=3), None)
    line_id = order.lines[0].id
    await service.add_reception(
        order.id,
        ReceptionCreate(units=[
            ReceptionUnitInput(purchase_order_line_id=line_id, vin="1HGCM82633A000001"),
            ReceptionUnitInput(purchase_order_line_id=line_id, vin="1HGCM82633A000002"),
            ReceptionUnitInput(purchase_order_line_id=line_id, vin="1HGCM82633A000003"),
        ]),
        None,
    )

    updated = await service.add_invoice(
        order.id,
        VehiclePurchaseOrderInvoiceCreate(
            invoice_number="F-001", total_amount=100.00, currency="USD", issued_at=date(2026, 9, 20)
        ),
        None,
    )

    assert updated.status == VehiclePurchaseOrderStatus.CONCILIADA
    units = session.scalars(select(DealershipVehicle).where(DealershipVehicle.purchase_order_line_id == line_id)).all()
    costs = sorted(float(u.cost_price) for u in units)
    assert costs == [33.33, 33.33, 33.34]  # remainder lands on one unit, sums to exactly 100.00
    assert sum(costs) == pytest.approx(100.00)
    assert all(not u.cost_is_estimated for u in units)


@pytest.mark.asyncio
async def test_partial_invoice_keeps_order_recibida_until_all_units_invoiced(env):
    service, _session, filial_id, supplier_id = env
    order = await service.create_vehicle_purchase_order(_order_payload(filial_id, supplier_id, quantity=2), None)
    line_id = order.lines[0].id
    received = await service.add_reception(
        order.id,
        ReceptionCreate(units=[
            ReceptionUnitInput(purchase_order_line_id=line_id, vin="1HGCM82633A000001"),
            ReceptionUnitInput(purchase_order_line_id=line_id, vin="1HGCM82633A000002"),
        ]),
        None,
    )
    assert received.status == VehiclePurchaseOrderStatus.RECIBIDA
    first_unit_id = received.receptions[0].units[0].id

    updated = await service.add_invoice(
        order.id,
        VehiclePurchaseOrderInvoiceCreate(
            invoice_number="F-002", total_amount=50.00, currency="USD", issued_at=date(2026, 9, 20),
            dealership_vehicle_ids=[first_unit_id],
        ),
        None,
    )

    assert updated.status == VehiclePurchaseOrderStatus.RECIBIDA


@pytest.mark.asyncio
async def test_invoicing_with_nothing_left_to_invoice_raises(env):
    service, _session, filial_id, supplier_id = env
    order = await service.create_vehicle_purchase_order(_order_payload(filial_id, supplier_id, quantity=1), None)

    with pytest.raises(NoUnitsToInvoiceError):
        await service.add_invoice(
            order.id,
            VehiclePurchaseOrderInvoiceCreate(
                invoice_number="F-003", total_amount=10.00, currency="USD", issued_at=date(2026, 9, 20)
            ),
            None,
        )


@pytest.mark.asyncio
async def test_cannot_cancel_a_fully_received_order(env):
    service, _session, filial_id, supplier_id = env
    order = await service.create_vehicle_purchase_order(_order_payload(filial_id, supplier_id, quantity=1), None)
    line_id = order.lines[0].id
    await service.add_reception(
        order.id,
        ReceptionCreate(units=[ReceptionUnitInput(purchase_order_line_id=line_id, vin="1HGCM82633A000001")]),
        None,
    )

    with pytest.raises(InvalidVehiclePurchaseOrderStatusTransitionError):
        await service.cancel_vehicle_purchase_order(order.id)


@pytest.mark.asyncio
async def test_can_cancel_before_full_reception(env):
    service, _session, filial_id, supplier_id = env
    order = await service.create_vehicle_purchase_order(_order_payload(filial_id, supplier_id), None)

    cancelled = await service.cancel_vehicle_purchase_order(order.id)

    assert cancelled.status == VehiclePurchaseOrderStatus.CANCELADA
