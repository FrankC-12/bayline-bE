"""Collections reduce receivables independently of warehouse completion."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from test_part_sales_fifo import AsyncAdapter
from test_receivables_include_part_sales import env as receivables_env
from test_receivables_include_part_sales import make_part_sale

from app.core.exceptions import BadRequestError
from app.core.venezuela_time import venezuela_today
from app.modules.administracion.enums import AccountCurrency, AccountType, IncomeSource
from app.modules.administracion.models import Account, IncomeEntry
from app.modules.exchange_rates.models import ExchangeRate
from app.modules.parts.enums import PartSaleStatus
from app.modules.parts.schemas import PartSalePaymentCreate, PartSalePaymentQuoteInput
from app.modules.parts.service import PartsService

collection_env = receivables_env


def account(session, filial_id, currency=AccountCurrency.USD, **kwargs):
    item = Account(
        filial_id=filial_id,
        name=str(uuid.uuid4()),
        currency=currency,
        account_type=AccountType.CAJA,
        **kwargs,
    )
    session.add(item)
    session.commit()
    return item


def payment(account_id, amount, **kwargs):
    return PartSalePaymentCreate(
        payment_method="usd",
        paid_usd=Decimal(amount),
        paid_bs=0,
        usd_account_id=account_id,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_50_dollar_payment_leaves_80_and_full_payment_clears_receivable(collection_env):
    admin, session, filial_id = collection_env
    sale = make_part_sale(session, filial_id, 5001, PartSaleStatus.COMPLETADO, 130)
    chosen = account(session, filial_id)
    parts = PartsService(AsyncAdapter(session))
    result = await parts.collect_sale_payment(sale.id, payment(chosen.id, "50"), None)
    assert result.amount_collected == 50
    assert result.pending_amount == 80
    receivable = (await admin.list_receivables(filial_id))[0]
    assert receivable.pending_amount == 80
    income = session.scalars(select(IncomeEntry)).one()
    assert income.account_id == chosen.id
    assert income.source == IncomeSource.AUTOMATICO
    assert income.amount_usd == 50
    await parts.collect_sale_payment(sale.id, payment(chosen.id, "80"), None)
    assert (await parts.get_sale(sale.id)).pending_amount == 0
    assert await admin.list_receivables(filial_id) == []
    with pytest.raises(BadRequestError):
        await parts.collect_sale_payment(sale.id, payment(chosen.id, "1"), None)


@pytest.mark.asyncio
async def test_bs_uses_frozen_rate_and_no_igtf(collection_env):
    admin, session, filial_id = collection_env
    sale = make_part_sale(session, filial_id, 5001, PartSaleStatus.PEDIDO, 100)
    sale.iva_amount = 16
    sale.igtf_percentage = 3
    rate = ExchangeRate(currency="USD", value_date=venezuela_today(), rate_ves=40)
    session.add(rate)
    chosen = account(session, filial_id, AccountCurrency.BS)
    parts = PartsService(AsyncAdapter(session))
    result = await parts.collect_sale_payment(
        sale.id,
        PartSalePaymentCreate(
            payment_method="bs", paid_usd=0, paid_bs=2000, bs_account_id=chosen.id
        ),
        None,
    )
    assert result.pending_amount == 66
    assert result.igtf_amount == 0
    income = session.scalars(select(IncomeEntry)).one()
    assert income.amount_usd == 50
    assert income.amount_bs == 2000
    assert income.exchange_rate == 40
    rate.rate_ves = 80
    session.commit()
    assert (await admin.list_receivables(filial_id))[0].pending_amount == 66


@pytest.mark.asyncio
async def test_mixed_payment_igtf_only_on_usd_and_partial_collections_accumulate(collection_env):
    admin, session, filial_id = collection_env
    sale = make_part_sale(session, filial_id, 5001, PartSaleStatus.PEDIDO, 100)
    sale.iva_amount = 16
    sale.igtf_percentage = 3
    session.add(ExchangeRate(currency="USD", value_date=venezuela_today(), rate_ves=40))
    usd_account = account(session, filial_id)
    bs_account = account(session, filial_id, AccountCurrency.BS)
    parts = PartsService(AsyncAdapter(session))
    quote = await parts.quote_sale_payment(
        sale.id, PartSalePaymentQuoteInput(payment_method="mixed", usd_base=50)
    )
    assert quote.due_usd == 51.5
    assert quote.due_bs == 2640
    assert quote.igtf_amount == 1.5
    result = await parts.collect_sale_payment(
        sale.id,
        PartSalePaymentCreate(
            payment_method="mixed",
            usd_base=50,
            paid_usd=25.75,
            paid_bs=1000,
            usd_account_id=usd_account.id,
            bs_account_id=bs_account.id,
        ),
        None,
    )
    assert result.igtf_amount == Decimal("0.75")
    assert result.pending_amount == 66
    result = await parts.collect_sale_payment(sale.id, payment(usd_account.id, "67.98"), None)
    assert result.pending_amount == 0
    assert result.igtf_amount == Decimal("2.73")
    assert await admin.list_receivables(filial_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", ["0", "131"])
async def test_zero_and_excess_payments_are_rejected(collection_env, amount):
    _, session, filial_id = collection_env
    sale = make_part_sale(session, filial_id, 5001, PartSaleStatus.PENDIENTE, 130)
    chosen = account(session, filial_id)
    with pytest.raises(BadRequestError):
        await PartsService(AsyncAdapter(session)).collect_sale_payment(
            sale.id, payment(chosen.id, amount), None
        )
    assert session.scalars(select(IncomeEntry)).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["foreign", "inactive", "wrong_currency", "cancelled"])
async def test_invalid_account_or_cancelled_sale_cannot_collect(collection_env, kind):
    _, session, filial_id = collection_env
    sale = make_part_sale(
        session,
        filial_id,
        5001,
        PartSaleStatus.CANCELADO if kind == "cancelled" else PartSaleStatus.PEDIDO,
        130,
    )
    chosen = account(
        session,
        uuid.uuid4() if kind == "foreign" else filial_id,
        AccountCurrency.BS if kind == "wrong_currency" else AccountCurrency.USD,
        is_active=kind != "inactive",
    )
    with pytest.raises(BadRequestError):
        await PartsService(AsyncAdapter(session)).collect_sale_payment(
            sale.id, payment(chosen.id, "50"), None
        )
    assert session.scalars(select(IncomeEntry)).all() == []


@pytest.mark.asyncio
async def test_parts_edit_permission_does_not_grant_collection(monkeypatch):
    from app.modules.auth.exceptions import InsufficientPermissionsError
    from app.modules.parts import router

    calls = []

    async def access(db, user, filial_id, module, level):
        calls.append(module)
        if module == "finanzas-cobrar":
            raise InsufficientPermissionsError()

    monkeypatch.setattr(router, "ensure_module_access", access)
    with pytest.raises(InsufficientPermissionsError):
        await router._ensure_sale_payment_access(object(), uuid.uuid4(), object())
    assert calls == ["finanzas-cobrar"]


@pytest.mark.asyncio
async def test_missing_bcv_rate_rejects_bs_without_creating_income(collection_env):
    _, session, filial_id = collection_env
    sale = make_part_sale(session, filial_id, 5001, PartSaleStatus.PEDIDO, 130)
    chosen = account(session, filial_id, AccountCurrency.BS)
    with pytest.raises(BadRequestError, match="tasa BCV"):
        await PartsService(AsyncAdapter(session)).collect_sale_payment(
            sale.id,
            PartSalePaymentCreate(
                payment_method="bs", paid_usd=0, paid_bs=100, bs_account_id=chosen.id
            ),
            None,
        )
    assert session.scalars(select(IncomeEntry)).all() == []


@pytest.mark.asyncio
async def test_sale_with_collections_cannot_be_cancelled(collection_env):
    _, session, filial_id = collection_env
    sale = make_part_sale(session, filial_id, 5001, PartSaleStatus.PEDIDO, 130)
    chosen = account(session, filial_id)
    parts = PartsService(AsyncAdapter(session))
    await parts.collect_sale_payment(sale.id, payment(chosen.id, "50"), None)
    with pytest.raises(BadRequestError, match="cobros registrados"):
        await parts.update_sale_status(sale.id, PartSaleStatus.CANCELADO)
    assert sale.status == PartSaleStatus.PEDIDO


def test_migration_removes_assumed_igtf_and_preserves_historical_collections(
    collection_env, monkeypatch
):
    import importlib.util
    from pathlib import Path

    from sqlalchemy import text

    _, session, filial_id = collection_env
    unpaid = make_part_sale(session, filial_id, 5001, PartSaleStatus.PEDIDO, 100)
    paid = make_part_sale(session, filial_id, 5002, PartSaleStatus.COMPLETADO, 100)
    for sale in (unpaid, paid):
        sale.iva_amount = 16
        sale.igtf_percentage = 3
        sale.igtf_amount = 3.48
    chosen = account(session, filial_id)
    session.add(
        IncomeEntry(
            filial_id=filial_id,
            entry_date=venezuela_today(),
            source=IncomeSource.AUTOMATICO,
            description="Historic sale",
            amount=119.48,
            currency=AccountCurrency.USD,
            account_id=chosen.id,
            source_type="PART_SALE",
            source_id=paid.id,
        )
    )
    session.commit()
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/b6d2e8f4c901_part_sale_collection_taxes.py"
    )
    spec = importlib.util.spec_from_file_location("collection_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(migration.op, "execute", lambda sql: session.execute(text(sql)))
    migration.upgrade()
    session.expire_all()
    assert unpaid.igtf_amount == 0
    assert paid.igtf_amount == Decimal("3.48")
    migration.downgrade()
    session.expire_all()
    assert unpaid.igtf_amount == Decimal("3.48")
    assert paid.igtf_amount == Decimal("3.48")
