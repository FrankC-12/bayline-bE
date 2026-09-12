"""A new account can start with the balance it already carried before
Bayline began tracking its movements — opening_balance is added once at
creation and folded into the account's balance alongside its income/expense
entries, but is never itself an Income/Expense entry."""

import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.administracion.enums import AccountCurrency, AccountType, CounterpartyType, IncomeConcept
from app.modules.administracion.schemas import AccountCreate, IncomeEntryCreate
from app.modules.administracion.service import AdministracionService
from app.modules.filiales.models import Filial


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        session.commit()
        db = AsyncAdapter(session)
        yield AdministracionService(db), session, filial_id


@pytest.mark.asyncio
async def test_opening_balance_defaults_to_zero(env):
    service, _, filial_id = env
    account = await service.create_account(
        AccountCreate(filial_id=filial_id, name="Caja", currency=AccountCurrency.USD, account_type=AccountType.CAJA)
    )
    assert float(account.opening_balance) == 0

    rows = await service.list_accounts(filial_id)
    assert rows[0]["balance"] == 0


@pytest.mark.asyncio
async def test_opening_balance_is_included_in_account_balance(env):
    service, _, filial_id = env
    account = await service.create_account(
        AccountCreate(
            filial_id=filial_id, name="Caja", currency=AccountCurrency.USD, account_type=AccountType.CAJA,
            opening_balance=500,
        )
    )
    assert float(account.opening_balance) == 500

    rows = await service.list_accounts(filial_id)
    assert rows[0]["balance"] == 500
    assert rows[0]["opening_balance"] == 500

    payload = IncomeEntryCreate(
        filial_id=filial_id, entry_date=date.today(), concept=IncomeConcept.OTRO_INGRESO,
        description="Ingreso", amount=50, currency=AccountCurrency.USD, account_id=account.id,
        counterparty_type=CounterpartyType.SOCIO, counterparty_name="Socio Uno",
    )
    await service.create_income(payload, None, uuid.uuid4())

    rows = await service.list_accounts(filial_id)
    assert rows[0]["balance"] == 550
