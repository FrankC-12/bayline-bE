"""Account-detail screen: a single, chronologically merged feed of an
account's income/expense entries, each carrying a polymorphic source_type/
source_id back at whatever document generated it (a resolved SupplierClaim,
an automatic sale) so the frontend can open the real source document."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.administracion.enums import (
    AccountCurrency,
    AccountType,
    ClaimResolution,
    ClaimStatus,
    CounterpartyType,
    IncomeConcept,
    MovementSourceType,
    SupplierType,
)
from app.modules.administracion.models import ExpenseEntry, Supplier, SupplierClaim
from app.modules.administracion.schemas import (
    AccountCreate,
    IncomeEntryCreate,
    SupplierClaimResolveInput,
)
from app.modules.administracion.service import AdministracionService
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part


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
async def test_record_automatic_income_stores_source(env):
    service, _, filial_id = env
    account = await service.create_account(
        AccountCreate(filial_id=filial_id, name="Caja", currency=AccountCurrency.USD, account_type=AccountType.CAJA)
    )
    sale_id = uuid.uuid4()
    entry = await service.record_automatic_income(
        filial_id, "Cierre de venta de repuestos", 80, "VR-1",
        source_type=MovementSourceType.PART_SALE, source_id=sale_id,
    )
    assert entry.source_type == MovementSourceType.PART_SALE
    assert entry.source_id == sale_id

    movements = await service.get_account_movements(account.id)
    assert len(movements) == 1
    assert movements[0]["source_type"] == MovementSourceType.PART_SALE
    assert movements[0]["source_id"] == sale_id
    assert movements[0]["movement_type"] == "ingreso"


@pytest.mark.asyncio
async def test_resolve_claim_stores_source(env):
    service, session, filial_id = env
    account = await service.create_account(
        AccountCreate(filial_id=filial_id, name="Caja", currency=AccountCurrency.USD, account_type=AccountType.CAJA)
    )
    part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Repuesto", price=10)
    supplier = Supplier(filial_id=filial_id, business_name="Proveedor S", rif="J-1", supplier_type=SupplierType.IMPORTADOR)
    session.add_all([part, supplier])
    session.commit()

    claim = SupplierClaim(
        filial_id=filial_id, part_id=part.id, quantity=1, supplier_id=supplier.id,
        status=ClaimStatus.RECHAZADO, claimed_amount=40, currency=AccountCurrency.USD,
    )
    session.add(claim)
    session.commit()

    payload = SupplierClaimResolveInput(resolution=ClaimResolution.COSTO_TALLER, account_id=account.id)
    resolved = await service.resolve_claim(claim.id, payload, uuid.uuid4())

    expense = await service.db.get(ExpenseEntry, resolved.expense_entry_id)
    assert expense.source_type == MovementSourceType.SUPPLIER_CLAIM
    assert expense.source_id == claim.id

    movements = await service.get_account_movements(account.id)
    assert movements[0]["movement_type"] == "egreso"
    assert movements[0]["source_type"] == MovementSourceType.SUPPLIER_CLAIM
    assert movements[0]["source_id"] == claim.id


@pytest.mark.asyncio
async def test_get_account_movements_merges_and_sorts_descending(env):
    service, _, filial_id = env
    account = await service.create_account(
        AccountCreate(filial_id=filial_id, name="Caja", currency=AccountCurrency.USD, account_type=AccountType.CAJA)
    )
    yesterday = date.today() - timedelta(days=1)

    await service.record_automatic_income(filial_id, "Venta vieja", 10, "VR-0")

    # Manual income dated today, plus a manual expense dated yesterday —
    # confirm the feed comes back income+expense merged, newest first.
    income_payload = IncomeEntryCreate(
        filial_id=filial_id, entry_date=date.today(), concept=IncomeConcept.OTRO_INGRESO,
        description="Ingreso manual", amount=30, currency=AccountCurrency.USD, account_id=account.id,
        counterparty_type=CounterpartyType.SOCIO, counterparty_name="Socio Uno",
    )
    await service.create_income(income_payload, None, uuid.uuid4())

    from app.modules.administracion.enums import ExpenseCategory

    expense = ExpenseEntry(
        filial_id=filial_id, entry_date=yesterday, category=ExpenseCategory.OTRO, beneficiary="Alguien",
        description="Gasto viejo", amount=5, currency=AccountCurrency.USD, account_id=account.id,
    )
    service.db.add(expense)
    await service.db.commit()

    movements = await service.get_account_movements(account.id)
    assert len(movements) == 3
    dates = [m["entry_date"] for m in movements]
    assert dates == sorted(dates, reverse=True)
    assert movements[-1]["movement_type"] == "egreso"


@pytest.mark.asyncio
async def test_get_account_detail_matches_list_accounts_row(env):
    service, _, filial_id = env
    account = await service.create_account(
        AccountCreate(
            filial_id=filial_id, name="Caja", currency=AccountCurrency.USD, account_type=AccountType.CAJA,
            opening_balance=200,
        )
    )
    detail = await service.get_account_detail(account.id)
    assert detail["id"] == account.id
    assert detail["opening_balance"] == 200
    assert detail["balance"] == 200
