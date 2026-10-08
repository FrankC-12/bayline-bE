"""Payments must remain atomic and serialized across independent PostgreSQL connections."""

import asyncio
from decimal import Decimal

import pytest
from conftest import invoice_input
from sqlalchemy import func, select

from app.core.exceptions import BadRequestError, ConflictError
from app.modules.administracion.models import IncomeEntry
from app.modules.service_orders.billing import BillingService
from app.modules.service_orders.billing_schemas import CollectInvoicePaymentInput
from app.modules.service_orders.models import ServiceOrder, ServiceOrderInvoice

pytestmark = pytest.mark.asyncio


async def issue(ctx, payload):
    async with ctx.sessions() as db:
        return await BillingService(db).issue(ctx.order_id, payload, None)


async def incomes(ctx):
    async with ctx.sessions() as db:
        return list(
            (
                await db.scalars(select(IncomeEntry).where(IncomeEntry.source_id == ctx.order_id))
            ).all()
        )


@pytest.mark.parametrize("method", ["usd", "bs", "mixed"])
async def test_issue_retry_freezes_one_invoice_and_income_set(billing_db, method):
    ctx = billing_db
    payload = await invoice_input(ctx, method=method)
    first = await issue(ctx, payload)
    second = await issue(ctx, payload)
    assert first.id == second.id
    assert first.document == second.document
    entries = await incomes(ctx)
    assert len(entries) == (2 if method == "mixed" else 1)
    assert {e.account_id for e in entries} <= {ctx.usd_account_id, ctx.bs_account_id}
    async with ctx.sessions() as db:
        assert await BillingService(db).list_receivables(ctx.filial_id) == []


async def test_simultaneous_same_request_creates_one_invoice(billing_db):
    ctx = billing_db
    payload = await invoice_input(ctx)
    first, second = await asyncio.gather(issue(ctx, payload), issue(ctx, payload))
    assert first.id == second.id
    assert len(await incomes(ctx)) == 1


async def test_simultaneous_different_requests_cannot_double_bill(billing_db):
    ctx = billing_db
    one, two = await invoice_input(ctx), await invoice_input(ctx)
    results = await asyncio.gather(issue(ctx, one), issue(ctx, two), return_exceptions=True)
    assert sum(isinstance(r, ServiceOrderInvoice) for r in results) == 1
    assert sum(isinstance(r, ConflictError) for r in results) == 1
    assert len(await incomes(ctx)) == 1


async def test_partial_collections_from_two_sessions_accumulate(billing_db):
    ctx = billing_db
    invoice = await issue(ctx, await invoice_input(ctx, paid=False))
    payload = CollectInvoicePaymentInput(
        account_id=ctx.usd_account_id, withholding_amount=0, net_collected_amount=10
    )

    async def collect():
        async with ctx.sessions() as db:
            return await BillingService(db).collect_invoice(invoice.id, payload, None)

    await asyncio.gather(collect(), collect())
    entries = await incomes(ctx)
    assert len(entries) == 2
    async with ctx.sessions() as db:
        receivable = (await BillingService(db).list_receivables(ctx.filial_id))[0]
        assert receivable.pending_amount == pytest.approx(float(invoice.total_usd) - 20)


async def test_simultaneous_overpayment_accepts_only_one_collection(billing_db):
    ctx = billing_db
    invoice = await issue(ctx, await invoice_input(ctx, paid=False))
    payload = CollectInvoicePaymentInput(
        account_id=ctx.usd_account_id, withholding_amount=0, net_collected_amount=40
    )

    async def collect():
        async with ctx.sessions() as db:
            return await BillingService(db).collect_invoice(invoice.id, payload, None)

    results = await asyncio.gather(collect(), collect(), return_exceptions=True)
    assert sum(isinstance(r, BadRequestError) for r in results) == 1
    assert len(await incomes(ctx)) == 1
    async with ctx.sessions() as db:
        pending = (await BillingService(db).list_receivables(ctx.filial_id))[0].pending_amount
        assert pending == pytest.approx(float(invoice.total_usd) - 40)


async def test_failure_after_flush_rolls_back_invoice_income_and_order(billing_db, monkeypatch):
    ctx = billing_db
    payload = await invoice_input(ctx)
    async with ctx.sessions() as db:

        async def fail_commit():
            await db.flush()
            raise RuntimeError("Injected failure after INSERT")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="Injected failure"):
            await BillingService(db).issue(ctx.order_id, payload, None)
    assert await incomes(ctx) == []
    async with ctx.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ServiceOrderInvoice)
                .where(ServiceOrderInvoice.service_order_id == ctx.order_id)
            )
            == 0
        )
        order = await db.get(ServiceOrder, ctx.order_id)
        assert order.invoiced_at is None and order.pricing_snapshot is None
    assert (await issue(ctx, payload)).id


async def test_same_collection_retry_is_recorded_once_even_after_full_payment(billing_db):
    import uuid

    ctx = billing_db
    invoice = await issue(ctx, await invoice_input(ctx, paid=False))
    payload = CollectInvoicePaymentInput(
        request_id=uuid.uuid4(),
        account_id=ctx.usd_account_id,
        withholding_amount=0,
        net_collected_amount=str(invoice.total_usd),
    )

    async def collect():
        async with ctx.sessions() as db:
            return await BillingService(db).collect_invoice(invoice.id, payload, None)

    first, second = await asyncio.gather(collect(), collect())
    assert first == second
    assert first.pending_amount == 0
    assert len(await incomes(ctx)) == 1
    assert await collect() == first


async def test_collection_retry_key_cannot_be_reused_for_a_different_amount(billing_db):
    import uuid

    ctx = billing_db
    invoice = await issue(ctx, await invoice_input(ctx, paid=False))
    payload = CollectInvoicePaymentInput(
        request_id=uuid.uuid4(),
        account_id=ctx.usd_account_id,
        withholding_amount=0,
        net_collected_amount=10,
    )
    async with ctx.sessions() as db:
        await BillingService(db).collect_invoice(invoice.id, payload, None)
    async with ctx.sessions() as db:
        with pytest.raises(ConflictError):
            await BillingService(db).collect_invoice(
                invoice.id, payload.model_copy(update={"net_collected_amount": Decimal("20")}), None
            )
        # Failed collection must release its transaction so the session can be reused.
        assert not db.in_transaction()
    assert len(await incomes(ctx)) == 1


async def test_failed_collection_rolls_back_income_receipt_and_balance(billing_db, monkeypatch):
    import uuid

    from app.modules.service_orders.models import ServiceOrderCollectionRequest

    ctx = billing_db
    invoice = await issue(ctx, await invoice_input(ctx, paid=False))
    payload = CollectInvoicePaymentInput(
        request_id=uuid.uuid4(),
        account_id=ctx.usd_account_id,
        withholding_amount=0,
        net_collected_amount=10,
    )
    async with ctx.sessions() as db:

        async def fail_commit():
            await db.flush()
            raise RuntimeError("Injected collection failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="Injected collection failure"):
            await BillingService(db).collect_invoice(invoice.id, payload, None)
        assert not db.in_transaction()
    assert await incomes(ctx) == []
    async with ctx.sessions() as db:
        assert await db.get(ServiceOrderCollectionRequest, payload.request_id) is None
        receivable = (await BillingService(db).list_receivables(ctx.filial_id))[0]
        assert receivable.pending_amount == float(invoice.total_usd)
        assert (
            await BillingService(db).collect_invoice(invoice.id, payload, None)
        ).pending_amount == pytest.approx(float(invoice.total_usd) - 10)
    assert len(await incomes(ctx)) == 1


async def test_issued_document_survives_catalog_and_customer_changes(billing_db):
    from app.modules.clients.models import Client
    from app.modules.post_ventas.models import LaborSettings

    ctx = billing_db
    payload = await invoice_input(ctx)
    original = await issue(ctx, payload)
    async with ctx.sessions() as db:
        client = await db.get(Client, original.billed_client_id)
        client.full_name = "Nombre modificado"
        settings = (
            await db.scalars(select(LaborSettings).where(LaborSettings.filial_id == ctx.filial_id))
        ).one()
        settings.hourly_rate = 999
        settings.iva_percentage = 0
        await db.commit()
    retried = await issue(ctx, payload)
    assert retried.document == original.document
    assert retried.document["client_name"] == "Cliente integración"
    assert len(await incomes(ctx)) == 1
