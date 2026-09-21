"""Billing a service order to a different client than the vehicle's owner
(e.g. the holding, registered as a regular empresa client) with less than
full payment at issuance, creating a receivable that later gets collected
with withholdings — and the holding-scoped consolidated report over it."""

import uuid
from decimal import Decimal

import pytest
from billing_support import configure_billing, invoice_payload
from test_part_sales_fifo import inventory as fifo_inventory
from test_parts_discounts import order_inventory as discount_inventory

from app.core.exceptions import BadRequestError
from app.modules.administracion.enums import AccountCurrency, AccountType
from app.modules.administracion.models import Account, IncomeEntry
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client
from app.modules.filiales.models import Filial
from app.modules.service_orders.billing_schemas import BillingInput, CollectInvoicePaymentInput, InvoiceCreate
from app.modules.service_orders.enums import ServiceOrderStatus

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


def make_holding_client(session, filial_id, is_holding_billing=True, document_number="123456789"):
    client = Client(
        filial_id=filial_id,
        full_name="Grupo Holding C.A." if is_holding_billing else "Cliente Regular C.A.",
        client_type=ClientType.EMPRESA,
        document_type=DocumentType.J,
        document_number=document_number,
        phone_primary="02121234567",
        address="Caracas",
        is_holding_billing=is_holding_billing,
    )
    session.add(client)
    session.commit()
    return client


@pytest.mark.asyncio
async def test_bill_to_a_different_client_with_zero_payment_creates_a_receivable(ready):
    await prepare(ready)
    _, session, order, _, _, billing, accounts, _ = ready
    holding_client = make_holding_client(session, order.filial_id)

    payload = await invoice_payload(billing, order, accounts)
    payload = payload.model_copy(
        update={
            "billed_client_id": holding_client.id,
            "paid_usd": Decimal("0"),
            "paid_bs": Decimal("0"),
            "client_confirmed": True,
            "client_confirmed_note": "Firmó el propietario del vehículo",
        }
    )

    invoice = await billing.issue(order.id, payload, None)

    assert invoice.billed_client_id == holding_client.id
    assert invoice.amount_paid_at_issuance == 0
    assert invoice.collected_at is None
    assert invoice.client_confirmed_at is not None
    assert invoice.client_confirmed_note == "Firmó el propietario del vehículo"

    receivables = await billing.list_receivables(order.filial_id)
    assert len(receivables) == 1
    assert receivables[0].invoice_id == invoice.id
    assert receivables[0].billed_client_name == "Grupo Holding C.A."
    assert receivables[0].pending_amount == float(invoice.total_usd)


@pytest.mark.asyncio
async def test_billing_to_a_client_in_another_filial_is_rejected(ready):
    await prepare(ready)
    _, session, order, _, _, billing, accounts, _ = ready
    other_filial_id = uuid.uuid4()
    session.add(Filial(id=other_filial_id, holding_id=uuid.uuid4(), name="Otra filial", slug="otra"))
    session.commit()
    foreign_client = make_holding_client(session, other_filial_id)

    payload = await invoice_payload(billing, order, accounts)
    payload = payload.model_copy(
        update={"billed_client_id": foreign_client.id, "paid_usd": Decimal("0"), "paid_bs": Decimal("0")}
    )

    with pytest.raises(BadRequestError):
        await billing.issue(order.id, payload, None)


@pytest.mark.asyncio
async def test_full_payment_to_a_different_client_is_already_collected(ready):
    await prepare(ready)
    _, session, order, _, _, billing, accounts, _ = ready
    holding_client = make_holding_client(session, order.filial_id)

    payload = await invoice_payload(billing, order, accounts)
    payload = payload.model_copy(update={"billed_client_id": holding_client.id})

    invoice = await billing.issue(order.id, payload, None)

    assert invoice.collected_at is not None
    assert await billing.list_receivables(order.filial_id) == []


@pytest.mark.asyncio
async def test_collect_invoice_posts_income_and_clears_the_receivable(ready):
    await prepare(ready)
    _, session, order, _, _, billing, accounts, _ = ready
    holding_client = make_holding_client(session, order.filial_id)

    payload = await invoice_payload(billing, order, accounts)
    payload = payload.model_copy(
        update={"billed_client_id": holding_client.id, "paid_usd": Decimal("0"), "paid_bs": Decimal("0")}
    )
    invoice = await billing.issue(order.id, payload, None)

    collection_account = Account(
        filial_id=order.filial_id, name="Cobranza", currency=AccountCurrency.USD, account_type=AccountType.CAJA
    )
    session.add(collection_account)
    session.commit()

    collected_by = uuid.uuid4()
    result = await billing.collect_invoice(
        invoice.id,
        CollectInvoicePaymentInput(
            account_id=collection_account.id,
            withholding_amount=Decimal("2.00"),
            net_collected_amount=Decimal("16.64"),
        ),
        collected_by,
    )

    assert result.pending_amount == float(invoice.total_usd) - 0.0
    assert await billing.list_receivables(order.filial_id) == []

    refreshed = await billing.get_invoice_by_id(invoice.id)
    assert refreshed.collected_at is not None
    assert refreshed.collected_by_user_id == collected_by
    assert float(refreshed.withholding_amount) == 2.00
    assert float(refreshed.net_collected_amount) == 16.64

    income = session.get(IncomeEntry, refreshed.collection_income_entry_id)
    assert income is not None
    assert float(income.amount) == 16.64
    assert income.account_id == collection_account.id


@pytest.mark.asyncio
async def test_collect_invoice_rejects_a_non_usd_account(ready):
    await prepare(ready)
    _, session, order, _, _, billing, accounts, _ = ready
    holding_client = make_holding_client(session, order.filial_id)
    payload = await invoice_payload(billing, order, accounts)
    payload = payload.model_copy(
        update={"billed_client_id": holding_client.id, "paid_usd": Decimal("0"), "paid_bs": Decimal("0")}
    )
    invoice = await billing.issue(order.id, payload, None)

    bs_account = Account(
        filial_id=order.filial_id, name="Caja Bs", currency=AccountCurrency.BS, account_type=AccountType.CAJA
    )
    session.add(bs_account)
    session.commit()

    with pytest.raises(BadRequestError):
        await billing.collect_invoice(
            invoice.id,
            CollectInvoicePaymentInput(
                account_id=bs_account.id, withholding_amount=Decimal("0"), net_collected_amount=Decimal("18.64")
            ),
            uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_holding_consolidated_report_sums_only_holding_billed_invoices(ready):
    await prepare(ready)
    _, session, order, _, _, billing, accounts, _ = ready
    holding_id = uuid.uuid4()
    session.get(Filial, order.filial_id).holding_id = holding_id
    holding_client = make_holding_client(session, order.filial_id)
    regular_client = make_holding_client(
        session, order.filial_id, is_holding_billing=False, document_number="987654321"
    )

    # One invoice billed to the holding, unpaid at issuance (pending).
    payload = await invoice_payload(billing, order, accounts)
    payload = payload.model_copy(
        update={"billed_client_id": holding_client.id, "paid_usd": Decimal("0"), "paid_bs": Decimal("0")}
    )
    await billing.issue(order.id, payload, None)

    report = await billing.get_holding_warranty_receivables(holding_id)

    assert len(report.filiales) == 1
    row = report.filiales[0]
    assert row.filial_id == order.filial_id
    assert row.total_pending > 0
    assert row.total_collected == 0
    assert regular_client.id != holding_client.id


# IVA/ISLR withholding, registered at the moment of billing (not just at
# collection) since a contribuyente especial's retention is known when the
# invoice is issued, not discovered later.


@pytest.mark.asyncio
async def test_quote_computes_iva_and_islr_retention(ready):
    await prepare(ready)
    _, _session, order, _, _, billing, _accounts, _ = ready

    quote = await billing.quote(
        order.id,
        BillingInput(
            payment_method="usd", usd_base="0",
            iva_retention_percentage=Decimal("75"), islr_retention_percentage=Decimal("2"),
        ),
    )

    expected_iva_retention = round(quote.summary.iva_amount * 0.75, 2)
    pretax_base = quote.summary.parts_subtotal + quote.summary.labor_subtotal
    expected_islr_retention = round(pretax_base * 0.02, 2)

    assert quote.iva_retention_amount == pytest.approx(expected_iva_retention, abs=0.01)
    assert quote.islr_retention_amount == pytest.approx(expected_islr_retention, abs=0.01)
    assert quote.net_expected == pytest.approx(
        quote.total_usd - quote.iva_retention_amount - quote.islr_retention_amount, abs=0.01
    )


@pytest.mark.asyncio
async def test_zero_retention_by_default_matches_today_s_behavior(ready):
    await prepare(ready)
    _, _session, order, _, _, billing, _accounts, _ = ready

    quote = await billing.quote(order.id, BillingInput(payment_method="usd", usd_base="0"))

    assert quote.iva_retention_amount == 0
    assert quote.islr_retention_amount == 0
    assert quote.net_expected == quote.total_usd


@pytest.mark.asyncio
async def test_paying_the_net_expected_amount_settles_the_invoice_despite_retention(ready):
    await prepare(ready)
    _, session, order, _, _, billing, accounts, _ = ready

    quote = await billing.quote(
        order.id, BillingInput(payment_method="usd", usd_base="0", iva_retention_percentage=Decimal("75"))
    )
    assert quote.net_expected < quote.total_usd  # sanity: retention actually reduced what's owed

    payload = InvoiceCreate(
        payment_method="usd", usd_base="0", quote_hash=quote.quote_hash, request_id=uuid.uuid4(),
        paid_usd=Decimal(str(quote.net_expected)), paid_bs=Decimal("0"),
        usd_account_id=accounts[0].id, bs_account_id=accounts[1].id,
        iva_retention_percentage=Decimal("75"),
    )
    invoice = await billing.issue(order.id, payload, None)

    assert float(invoice.iva_retention_amount) == pytest.approx(quote.iva_retention_amount, abs=0.01)
    assert invoice.collected_at is not None  # settled — the withheld part was never expected as cash
    assert await billing.list_receivables(order.filial_id) == []


@pytest.mark.asyncio
async def test_partial_payment_with_retention_leaves_the_correct_pending_amount(ready):
    await prepare(ready)
    _, session, order, _, _, billing, accounts, _ = ready

    quote = await billing.quote(
        order.id, BillingInput(payment_method="usd", usd_base="0", iva_retention_percentage=Decimal("75"))
    )
    payload = InvoiceCreate(
        payment_method="usd", usd_base="0", quote_hash=quote.quote_hash, request_id=uuid.uuid4(),
        paid_usd=Decimal("0"), paid_bs=Decimal("0"),
        usd_account_id=accounts[0].id, bs_account_id=accounts[1].id,
        iva_retention_percentage=Decimal("75"),
    )
    await billing.issue(order.id, payload, None)

    receivables = await billing.list_receivables(order.filial_id)
    assert len(receivables) == 1
    assert receivables[0].pending_amount == pytest.approx(quote.net_expected, abs=0.01)
    assert receivables[0].iva_retention_amount == pytest.approx(quote.iva_retention_amount, abs=0.01)


@pytest.mark.asyncio
async def test_holding_report_counts_invoice_level_retentions_as_withheld(ready):
    await prepare(ready)
    _, session, order, _, _, billing, accounts, _ = ready
    holding_id = uuid.uuid4()
    session.get(Filial, order.filial_id).holding_id = holding_id
    holding_client = make_holding_client(session, order.filial_id)

    quote = await billing.quote(
        order.id, BillingInput(payment_method="usd", usd_base="0", iva_retention_percentage=Decimal("75"))
    )
    payload = InvoiceCreate(
        payment_method="usd", usd_base="0", quote_hash=quote.quote_hash, request_id=uuid.uuid4(),
        paid_usd=Decimal(str(quote.net_expected)), paid_bs=Decimal("0"),
        usd_account_id=accounts[0].id, bs_account_id=accounts[1].id,
        billed_client_id=holding_client.id, iva_retention_percentage=Decimal("75"),
    )
    await billing.issue(order.id, payload, None)

    report = await billing.get_holding_warranty_receivables(holding_id)

    assert report.filiales[0].total_withheld == pytest.approx(quote.iva_retention_amount, abs=0.01)
