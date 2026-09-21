"""ODS billing can now target a real Supplier (e.g. the manufacturer or a
parts supplier covering a warranty claim), not just a Client — resolved via
a linked billing Client, created on first use and reused after that, the
same idea already used for is_holding_billing."""

import uuid
from decimal import Decimal

import pytest
from billing_support import configure_billing
from pydantic import ValidationError
from test_part_sales_fifo import inventory as fifo_inventory
from test_parts_discounts import order_inventory as discount_inventory

from app.core.exceptions import BadRequestError
from app.modules.administracion.enums import SupplierStatus, SupplierType
from app.modules.administracion.models import Supplier
from app.modules.clients.models import Client
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.billing_schemas import BillingInput, InvoiceCreate
from app.modules.service_orders.models import ServiceOrder

inventory = fifo_inventory
order_inventory = discount_inventory


@pytest.fixture
def ready(order_inventory):
    service, session, order, part_id, lots = order_inventory
    billing, accounts, settings = configure_billing(service, session, order)
    return service, session, order, part_id, lots, billing, accounts, settings


async def prepare(ready):
    service, session, order, part_id, *_ = ready
    await service.add_transfer_line(order.id, part_id, 1)
    order.status = ServiceOrderStatus.COMPLETADO
    session.commit()


def make_supplier(session, filial_id, rif="J-12345678-9", business_name="Importadora XYZ"):
    supplier = Supplier(
        filial_id=filial_id, business_name=business_name, rif=rif,
        supplier_type=SupplierType.FABRICANTE, status=SupplierStatus.ACTIVO,
    )
    session.add(supplier)
    session.commit()
    return supplier


async def issue_to_supplier(billing, order, accounts, supplier_id):
    quote = await billing.quote(
        order.id, BillingInput(payment_method="usd", usd_base="0", billed_supplier_id=supplier_id)
    )
    invoice_input = InvoiceCreate(
        payment_method="usd",
        usd_base="0",
        billed_supplier_id=supplier_id,
        request_id=uuid.uuid4(),
        quote_hash=quote.quote_hash,
        paid_usd=Decimal(str(quote.due_usd)),
        paid_bs=Decimal(str(quote.due_bs)),
        usd_account_id=accounts[0].id,
        bs_account_id=accounts[1].id,
        payment_reference="REF-PROVEEDOR",
    )
    return await billing.issue(order.id, invoice_input, uuid.uuid4())


@pytest.mark.asyncio
async def test_billing_to_a_supplier_uses_its_business_name_and_rif(ready):
    await prepare(ready)
    _, session, order, _, _, billing, accounts, _ = ready
    supplier = make_supplier(session, order.filial_id)

    invoice = await issue_to_supplier(billing, order, accounts, supplier.id)

    assert invoice.document["client_name"] == "Importadora XYZ"
    assert invoice.document["client_document"] == "J-123456789"

    linked_client = session.get(Client, invoice.billed_client_id)
    assert linked_client.linked_supplier_id == supplier.id


@pytest.mark.asyncio
async def test_billing_the_same_supplier_twice_reuses_the_same_linked_client(ready):
    await prepare(ready)
    service, session, order, _, _, billing, accounts, _ = ready
    supplier = make_supplier(session, order.filial_id)

    first_invoice = await issue_to_supplier(billing, order, accounts, supplier.id)

    other_order = ServiceOrder(
        id=uuid.uuid4(), filial_id=order.filial_id, vehicle_id=order.vehicle_id, sequence_number=2002
    )
    session.add(other_order)
    session.commit()
    other_order.status = ServiceOrderStatus.COMPLETADO
    session.commit()

    second_invoice = await issue_to_supplier(billing, other_order, accounts, supplier.id)

    assert second_invoice.billed_client_id == first_invoice.billed_client_id
    linked_clients = session.query(Client).filter(Client.linked_supplier_id == supplier.id).all()
    assert len(linked_clients) == 1


@pytest.mark.asyncio
async def test_billing_input_rejects_both_client_and_supplier_at_once():
    with pytest.raises(ValidationError):
        BillingInput(
            payment_method="usd",
            billed_client_id=uuid.uuid4(),
            billed_supplier_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_billing_to_a_supplier_with_a_malformed_rif_fails_cleanly_not_a_500(ready):
    await prepare(ready)
    _, session, order, _, _, billing, accounts, _ = ready
    supplier = make_supplier(session, order.filial_id, rif="???", business_name="RIF Inválido")

    with pytest.raises(BadRequestError):
        await issue_to_supplier(billing, order, accounts, supplier.id)
