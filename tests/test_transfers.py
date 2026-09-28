"""S3 · Transferencias entre cuentas — a transfer moves money between two of
the filial's own accounts as a linked IncomeEntry/ExpenseEntry pair tagged
source_type=ACCOUNT_TRANSFER, so it updates both balances but never counts
as real income/expense or shows up in Ingresos, Egresos or Rentabilidad."""

import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.administracion.enums import AccountCurrency, AccountType, MovementSourceType
from app.modules.administracion.exceptions import SameAccountTransferError, TransferExchangeRateRequiredError
from app.modules.administracion.models import Account
from app.modules.administracion.schemas import TransferCreate
from app.modules.administracion.service import AdministracionService
from app.modules.filiales.models import Filial


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        cash_usd = Account(filial_id=filial_id, name="Efectivo", currency=AccountCurrency.USD, account_type=AccountType.CAJA)
        bank_usd = Account(filial_id=filial_id, name="Banco USD", currency=AccountCurrency.USD, account_type=AccountType.CORRIENTE)
        bank_bs = Account(filial_id=filial_id, name="Banplus", currency=AccountCurrency.BS, account_type=AccountType.CORRIENTE)
        session.add_all([cash_usd, bank_usd, bank_bs])
        session.commit()
        db = AsyncAdapter(session)
        yield AdministracionService(db), session, filial_id, cash_usd, bank_usd, bank_bs


def _payload(filial_id, from_account_id, to_account_id, **overrides) -> TransferCreate:
    data = dict(
        filial_id=filial_id,
        entry_date=date.today(),
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        amount=100,
    )
    data.update(overrides)
    return TransferCreate(**data)


@pytest.mark.asyncio
async def test_same_currency_transfer_moves_both_balances(env):
    service, session, filial_id, cash_usd, bank_usd, _bank_bs = env
    result = await service.create_transfer(_payload(filial_id, cash_usd.id, bank_usd.id, amount=100), uuid.uuid4())

    assert result.from_amount == 100
    assert result.to_amount == 100

    cash_balance, _ = await service._account_balance(cash_usd, 40.0)
    bank_balance, _ = await service._account_balance(bank_usd, 40.0)
    assert cash_balance == -100
    assert bank_balance == 100


@pytest.mark.asyncio
async def test_cross_currency_transfer_applies_given_rate(env):
    service, session, filial_id, cash_usd, _bank_usd, bank_bs = env
    result = await service.create_transfer(
        _payload(filial_id, cash_usd.id, bank_bs.id, amount=100, exchange_rate=40), uuid.uuid4()
    )

    assert result.from_amount == 100
    assert result.to_amount == 4000
    assert result.exchange_rate == 40

    bs_balance, bs_balance_usd = await service._account_balance(bank_bs, 40.0)
    assert bs_balance == 4000
    assert bs_balance_usd == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_cross_currency_transfer_without_rate_raises(env):
    service, session, filial_id, cash_usd, _bank_usd, bank_bs = env
    with pytest.raises(TransferExchangeRateRequiredError):
        await service.create_transfer(_payload(filial_id, cash_usd.id, bank_bs.id, amount=100), uuid.uuid4())


@pytest.mark.asyncio
async def test_same_account_transfer_raises(env):
    service, session, filial_id, cash_usd, _bank_usd, _bank_bs = env
    with pytest.raises(SameAccountTransferError):
        await service.create_transfer(_payload(filial_id, cash_usd.id, cash_usd.id, amount=100), uuid.uuid4())


@pytest.mark.asyncio
async def test_transfer_entries_link_to_each_other(env):
    service, session, filial_id, cash_usd, bank_usd, _bank_bs = env
    result = await service.create_transfer(_payload(filial_id, cash_usd.id, bank_usd.id, amount=100), uuid.uuid4())

    movements = await service.get_account_movements(cash_usd.id)
    assert len(movements) == 1
    assert movements[0]["source_type"] == MovementSourceType.ACCOUNT_TRANSFER
    assert movements[0]["source_id"] == result.to_entry_id

    movements = await service.get_account_movements(bank_usd.id)
    assert len(movements) == 1
    assert movements[0]["source_id"] == result.from_entry_id


@pytest.mark.asyncio
async def test_cross_currency_transfer_exposes_applied_rate_in_movements(env):
    service, session, filial_id, cash_usd, _bank_usd, bank_bs = env
    await service.create_transfer(
        _payload(filial_id, cash_usd.id, bank_bs.id, amount=100, exchange_rate=40), uuid.uuid4()
    )

    movements = await service.get_account_movements(bank_bs.id)
    assert movements[0]["exchange_rate"] == 40


@pytest.mark.asyncio
async def test_transfer_excluded_from_income_and_expense_lists(env):
    service, session, filial_id, cash_usd, bank_usd, _bank_bs = env
    await service.create_transfer(_payload(filial_id, cash_usd.id, bank_usd.id, amount=100), uuid.uuid4())

    assert await service.list_income(filial_id) == []
    assert await service.list_expenses(filial_id) == []


@pytest.mark.asyncio
async def test_transfer_excluded_from_dashboard_monthly_totals(env):
    service, session, filial_id, cash_usd, bank_usd, _bank_bs = env
    await service.create_transfer(_payload(filial_id, cash_usd.id, bank_usd.id, amount=100), uuid.uuid4())

    dashboard = await service.get_dashboard(filial_id)
    assert dashboard.income_month == 0
    assert dashboard.expense_month == 0


@pytest.mark.asyncio
async def test_transfer_excluded_from_operating_expenses(env):
    service, session, filial_id, cash_usd, bank_usd, _bank_bs = env
    await service.create_transfer(_payload(filial_id, cash_usd.id, bank_usd.id, amount=100), uuid.uuid4())

    report = await service.get_profitability(filial_id, date.today().replace(day=1), date.today())
    adjustment = next(a for a in report.adjustments if a.key == "gastos_operacionales")
    assert adjustment.amount == 0.0
