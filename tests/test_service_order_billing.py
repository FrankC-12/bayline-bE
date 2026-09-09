import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from billing_support import configure_billing, invoice_payload
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from test_part_sales_fifo import inventory as fifo_inventory
from test_parts_discounts import order_inventory as discount_inventory

from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import BadRequestError, ConflictError
from app.modules.administracion.models import IncomeEntry
from app.modules.auth.dependencies import get_current_user
from app.modules.exchange_rates.models import ExchangeRate
from app.modules.service_orders import router as routes
from app.modules.service_orders.billing import billing_day
from app.modules.service_orders.billing_schemas import BillingInput
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.exceptions import ServiceOrderReadOnlyError
from app.modules.service_orders.invoice_document import render_invoice
from app.modules.service_orders.models import ServiceOrderInvoice
from app.modules.service_orders.schemas import ServiceOrderUpdate

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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,usd_base,due_usd,due_bs,total",
    [
        ("usd", "0", "18.64", "0", "18.64"),
        ("bs", "0", "0", "905", "18.10"),
        ("mixed", "10", "10.30", "405", "18.40"),
    ],
)
async def test_capture_issue_freeze_then_close(ready, method, usd_base, due_usd, due_bs, total):
    await prepare(ready)
    service, session, order, _, lots, billing, accounts, settings = ready
    payload = await invoice_payload(billing, order, accounts, method, usd_base)
    assert payload.paid_usd == Decimal(due_usd)
    assert payload.paid_bs == Decimal(due_bs)
    invoice = await billing.issue(order.id, payload, None)
    assert order.status == ServiceOrderStatus.COMPLETADO
    assert order.closed_at is None
    assert order.invoiced_at is not None
    assert order.total_amount == Decimal(total)
    assert invoice.document["summary"]["parts_subtotal"] == 15.6
    assert round(invoice.document["summary"]["iva_amount"], 2) == 2.50
    assert invoice.document["summary"]["labor_subtotal"] == 0
    assert invoice.document["bcv_date"] == billing_day().isoformat()
    ledger = session.scalars(select(IncomeEntry)).all()
    assert sum(float(entry.amount) for entry in ledger if entry.currency.value == "usd") == float(
        due_usd
    )
    assert sum(float(entry.amount) for entry in ledger if entry.currency.value == "bs") == float(
        due_bs
    )
    original = render_invoice(invoice.document)
    assert "&lt;prueba&gt;" in original and "<prueba>" not in original
    settings.hourly_rate = 1000
    settings.iva_percentage = 25
    settings.igtf_percentage = 10
    session.scalar(select(ExchangeRate)).rate_ves = 999
    lots[-1].unit_cost = 1000
    session.commit()
    session.expire_all()
    assert render_invoice((await billing.get_invoice(order.id)).document) == original
    assert (await service.get_order_summary(order.id)).total == float(total)
    with pytest.raises(ServiceOrderReadOnlyError):
        await service.add_transfer_line(order.id, ready[3], 1)
    with pytest.raises(ServiceOrderReadOnlyError):
        await service.update_order(order.id, ServiceOrderUpdate(discount_label="Precio de costo"))
    await service.close_order(order.id)
    assert order.status == ServiceOrderStatus.ORDEN_CERRADA
    assert session.scalar(select(func.count()).select_from(IncomeEntry)) == len(ledger)
    # Retrying the same request returns the original document and never posts twice.
    assert (await billing.issue(order.id, payload, None)).id == invoice.id
    assert session.scalar(select(func.count()).select_from(IncomeEntry)) == len(ledger)


@pytest.mark.asyncio
async def test_mismatch_stale_quote_and_duplicate_are_atomic(ready):
    await prepare(ready)
    _, session, order, _, _, billing, accounts, settings = ready
    payload = await invoice_payload(billing, order, accounts)
    bad = payload.model_copy(update={"paid_usd": Decimal("1")})
    with pytest.raises(BadRequestError):
        await billing.issue(order.id, bad, None)
    assert order.invoiced_at is None
    assert session.scalar(select(func.count()).select_from(IncomeEntry)) == 0
    assert session.scalar(select(func.count()).select_from(ServiceOrderInvoice)) == 0
    settings.igtf_percentage = 5
    session.commit()
    with pytest.raises(ConflictError, match="cambiaron"):
        await billing.issue(order.id, payload, None)
    payload = await invoice_payload(billing, order, accounts)
    await billing.issue(order.id, payload, None)
    with pytest.raises(ConflictError):
        await billing.issue(order.id, payload.model_copy(update={"request_id": uuid.uuid4()}), None)
    assert session.scalar(select(func.count()).select_from(ServiceOrderInvoice)) == 1


@pytest.mark.asyncio
async def test_missing_or_stale_bcv_and_account_validation(ready):
    await prepare(ready)
    _, session, order, _, _, billing, accounts, _ = ready
    rate = session.scalar(select(ExchangeRate))
    rate.value_date = billing_day() - timedelta(days=1)
    session.commit()
    with pytest.raises(BadRequestError, match="BCV"):
        await billing.quote(order.id, BillingInput(payment_method="bs"))
    quote = await billing.quote(order.id, BillingInput(payment_method="usd"))
    assert quote.bcv_rate is None
    payload = await invoice_payload(billing, order, accounts)
    accounts[0].filial_id = uuid.uuid4()
    session.commit()
    with pytest.raises(BadRequestError, match="cuenta"):
        await billing.issue(order.id, payload, None)
    assert session.scalar(select(func.count()).select_from(IncomeEntry)) == 0


@pytest.mark.asyncio
async def test_http_billing_and_close_are_separate(ready, monkeypatch):
    await prepare(ready)
    service, _, order, _, _, billing, accounts, _ = ready
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    register_exception_handlers(app)
    app.dependency_overrides[routes.get_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=None)

    async def allowed(*args, **kwargs):
        pass

    monkeypatch.setattr(routes, "_ensure_access", allowed)
    url = f"/api/v1/service-orders/{order.id}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(url + "/close")).status_code == 400
        assert (await client.patch(url, json={"status": "orden_cerrada"})).status_code == 400
        assert (await client.get(url + "/billing")).status_code == 200
        quoted = await client.post(
            url + "/billing/quote", json={"payment_method": "mixed", "usd_base": "10"}
        )
        assert quoted.status_code == 200
        payload = await invoice_payload(billing, order, accounts, "mixed", "10")
        result = await client.post(url + "/invoice", json=payload.model_dump(mode="json"))
        assert result.status_code == 201
        assert order.status == ServiceOrderStatus.COMPLETADO
        document = await client.get(url + "/invoice/document")
        assert document.status_code == 200
        assert document.json()["filename"].endswith(".html")
        assert "Comprobante de pago" in document.json()["html"]
        assert (await client.patch(url, json={"notes": "Cambiar"})).status_code == 409
        assert (await client.post(url + "/close")).status_code == 200
        assert order.status == ServiceOrderStatus.ORDEN_CERRADA


@pytest.mark.asyncio
@pytest.mark.parametrize("usd_base", ["0", "18.10", "20"])
async def test_invalid_mixed_split(ready, usd_base):
    await prepare(ready)
    billing, order = ready[5], ready[2]
    with pytest.raises(BadRequestError):
        await billing.quote(order.id, BillingInput(payment_method="mixed", usd_base=usd_base))
