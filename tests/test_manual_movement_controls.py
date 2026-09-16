"""Manual Ingresos/Egresos movements: closed concept + counterparty, a BCV
rate frozen at save time, a supporting attachment above a configurable
threshold, a current-month-only period rule, and reversal-only correction
(no edit/delete route exists — never has). Also covers the "% manual
movements" KPI and confirms the five automatic-entry-creation paths that
must never be touched keep working with the new columns left null."""

import io
import uuid
from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exceptions import BadRequestError
from app.modules.administracion.enums import (
    AccountCurrency,
    AccountType,
    ClaimResolution,
    ClaimStatus,
    CounterpartyType,
    IncomeConcept,
    SupplierType,
)
from app.modules.administracion.exceptions import (
    AttachmentRequiredError,
    ClosedPeriodEntryDateError,
    EntryAlreadyReversedError,
    ExchangeRateRequiredError,
    FutureEntryDateError,
)
from app.modules.administracion.models import Account, ExpenseEntry, IncomeEntry, Supplier, SupplierClaim
from app.modules.administracion.router import router as administracion_router
from app.modules.administracion.schemas import ExpenseEntryCreate, IncomeEntryCreate
from app.modules.administracion.service import AdministracionService
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client
from app.modules.exchange_rates.models import ExchangeRate
from app.modules.filiales.models import Filial
from app.modules.kpis.service import KpiService
from app.modules.parts.models import Part


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        usd_account = Account(filial_id=filial_id, name="Caja USD", currency=AccountCurrency.USD, account_type=AccountType.CAJA)
        bs_account = Account(filial_id=filial_id, name="Caja Bs", currency=AccountCurrency.BS, account_type=AccountType.CAJA)
        client = Client(
            filial_id=filial_id, full_name="Cliente Uno", client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V, document_number="12345678", phone_primary="04121234567", address="Caracas",
        )
        session.add_all([usd_account, bs_account, client])
        session.commit()
        db = AsyncAdapter(session)
        yield AdministracionService(db), session, filial_id, usd_account, bs_account, client


def _income_payload(filial_id, account_id, **overrides) -> IncomeEntryCreate:
    data = dict(
        filial_id=filial_id,
        entry_date=date.today(),
        concept=IncomeConcept.OTRO_INGRESO,
        description="Ingreso de prueba",
        amount=50,
        currency=AccountCurrency.USD,
        account_id=account_id,
        counterparty_type=CounterpartyType.SOCIO,
        counterparty_name="Socio Uno",
    )
    data.update(overrides)
    return IncomeEntryCreate(**data)


def _expense_payload(filial_id, account_id, **overrides) -> ExpenseEntryCreate:
    from app.modules.administracion.enums import ExpenseCategory

    data = dict(
        filial_id=filial_id,
        entry_date=date.today(),
        category=ExpenseCategory.OTRO,
        beneficiary="Proveedor Varios",
        description="Gasto de prueba",
        amount=50,
        currency=AccountCurrency.USD,
        account_id=account_id,
        counterparty_type=CounterpartyType.SOCIO,
        counterparty_name="Socio Uno",
    )
    data.update(overrides)
    return ExpenseEntryCreate(**data)


# --- Schema-level validation: concept/counterparty/account are required ---


def test_income_create_requires_concept():
    with pytest.raises(ValidationError):
        IncomeEntryCreate(
            filial_id=uuid.uuid4(), entry_date=date.today(), description="x", amount=10,
            currency=AccountCurrency.USD, account_id=uuid.uuid4(),
            counterparty_type=CounterpartyType.SOCIO, counterparty_name="Socio",
        )


def test_income_create_requires_matching_counterparty_field():
    with pytest.raises(ValidationError):
        _income_payload(uuid.uuid4(), uuid.uuid4(), counterparty_type=CounterpartyType.CLIENTE, counterparty_name=None)


def test_income_create_cliente_counterparty_accepted():
    payload = _income_payload(
        uuid.uuid4(), uuid.uuid4(), counterparty_type=CounterpartyType.CLIENTE,
        counterparty_client_id=uuid.uuid4(), counterparty_name=None,
    )
    assert payload.counterparty_client_id is not None


# --- Exchange rate: frozen at entry_date, required for a non-USD entry ---


@pytest.mark.asyncio
async def test_bs_entry_without_rate_raises(env):
    service, _, filial_id, _, bs_account, _ = env
    payload = _income_payload(filial_id, bs_account.id, currency=AccountCurrency.BS, amount=100)
    with pytest.raises(ExchangeRateRequiredError):
        await service.create_income(payload, None, uuid.uuid4())


@pytest.mark.asyncio
async def test_bs_entry_with_rate_freezes_amounts(env):
    service, session, filial_id, _, bs_account, _ = env
    rate = ExchangeRate(currency="USD", rate_ves=40, value_date=date.today())
    session.add(rate)
    session.commit()

    payload = _income_payload(filial_id, bs_account.id, currency=AccountCurrency.BS, amount=400)
    entry = await service.create_income(payload, None, uuid.uuid4())

    assert float(entry.exchange_rate) == 40
    assert float(entry.amount_bs) == 400
    assert float(entry.amount_usd) == 10

    rate.rate_ves = 999
    session.commit()
    session.refresh(entry)
    assert float(entry.exchange_rate) == 40
    assert float(entry.amount_usd) == 10


@pytest.mark.asyncio
async def test_usd_entry_without_rate_saves_with_null_amount_bs(env):
    service, _, filial_id, usd_account, _, _ = env
    payload = _income_payload(filial_id, usd_account.id, currency=AccountCurrency.USD, amount=10)
    entry = await service.create_income(payload, None, uuid.uuid4())
    assert entry.exchange_rate is None
    assert entry.amount_bs is None
    assert float(entry.amount_usd) == 10


# --- Attachment threshold ---


@pytest.mark.asyncio
async def test_amount_over_threshold_without_attachment_raises(env):
    service, _, filial_id, usd_account, _, _ = env
    payload = _income_payload(filial_id, usd_account.id, amount=150)
    with pytest.raises(AttachmentRequiredError):
        await service.create_income(payload, None, uuid.uuid4())


@pytest.mark.asyncio
async def test_amount_under_threshold_without_attachment_saves(env):
    service, _, filial_id, usd_account, _, _ = env
    payload = _income_payload(filial_id, usd_account.id, amount=50)
    entry = await service.create_income(payload, None, uuid.uuid4())
    assert entry.attachment_url is None


@pytest.mark.asyncio
async def test_amount_over_threshold_with_attachment_saves(env, tmp_path, monkeypatch):
    import app.core.config as config_module

    patched_settings = config_module.get_settings().model_copy(update={"uploads_dir": str(tmp_path)})
    monkeypatch.setattr(config_module, "get_settings", lambda: patched_settings)

    service, _, filial_id, usd_account, _, _ = env
    payload = _income_payload(filial_id, usd_account.id, amount=150)
    attachment = UploadFile(filename="comprobante.png", file=io.BytesIO(b"fake-png"), headers={"content-type": "image/png"})
    entry = await service.create_income(payload, attachment, uuid.uuid4())
    assert entry.attachment_url is not None
    assert entry.attachment_url.startswith("/api/v1/uploads/manual-movements/")


# --- Period rule: current calendar month only, no future dates ---


@pytest.mark.asyncio
async def test_future_date_raises(env):
    service, _, filial_id, usd_account, _, _ = env
    payload = _income_payload(filial_id, usd_account.id, entry_date=date.today() + timedelta(days=1))
    with pytest.raises(FutureEntryDateError):
        await service.create_income(payload, None, uuid.uuid4())


@pytest.mark.asyncio
async def test_prior_month_date_raises(env):
    service, _, filial_id, usd_account, _, _ = env
    last_month = (date.today().replace(day=1) - timedelta(days=1))
    payload = _income_payload(filial_id, usd_account.id, entry_date=last_month)
    with pytest.raises(ClosedPeriodEntryDateError):
        await service.create_income(payload, None, uuid.uuid4())


# --- Reversal-only correction ---


@pytest.mark.asyncio
async def test_reverse_income_creates_negative_row_and_nets_balance(env):
    service, session, filial_id, usd_account, _, _ = env
    payload = _income_payload(filial_id, usd_account.id, amount=80)
    original = await service.create_income(payload, None, uuid.uuid4())

    reversal = await service.reverse_income(original.id, uuid.uuid4())
    assert reversal.reverses_entry_id == original.id
    assert float(reversal.amount) == -80
    assert reversal.entry_date == date.today()

    account = await service.get_account(usd_account.id)
    balance, _ = await service._account_balance(account, bcv_rate=1)
    assert balance == 0

    with pytest.raises(EntryAlreadyReversedError):
        await service.reverse_income(original.id, uuid.uuid4())


@pytest.mark.asyncio
async def test_reverse_expense_creates_negative_row(env):
    service, _, filial_id, usd_account, _, _ = env
    payload = _expense_payload(filial_id, usd_account.id, amount=30)
    original = await service.create_expense(payload, None, uuid.uuid4())

    reversal = await service.reverse_expense(original.id, uuid.uuid4())
    assert reversal.reverses_entry_id == original.id
    assert float(reversal.amount) == -30

    with pytest.raises(EntryAlreadyReversedError):
        await service.reverse_expense(original.id, uuid.uuid4())


# --- Regression: automatic-path entry creation is untouched ---


@pytest.mark.asyncio
async def test_record_automatic_income_still_works_untagged(env):
    service, _, filial_id, usd_account, _, _ = env
    entry = await service.record_automatic_income(filial_id, "Venta de repuestos", 25, "PV-1")
    assert entry is not None
    assert entry.concept is None
    assert entry.counterparty_type is None


@pytest.mark.asyncio
async def test_resolve_claim_costo_taller_still_works_untagged(env):
    service, session, filial_id, usd_account, _, _ = env
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

    from app.modules.administracion.schemas import SupplierClaimResolveInput

    payload = SupplierClaimResolveInput(resolution=ClaimResolution.COSTO_TALLER, account_id=usd_account.id)
    resolved = await service.resolve_claim(claim.id, payload, uuid.uuid4())

    expense = await service.db.get(ExpenseEntry, resolved.expense_entry_id)
    assert expense.counterparty_type is None


# --- KPI: % manual movements, excluding automatic-path rows ---


@pytest.mark.asyncio
async def test_manual_movements_rate_excludes_automatic_rows(env):
    service, session, filial_id, usd_account, _, _ = env
    kpi_service = KpiService(service.db)

    await service.record_automatic_income(filial_id, "Venta", 25, "PV-1")
    payload = _income_payload(filial_id, usd_account.id, amount=30)
    await service.create_income(payload, None, uuid.uuid4())

    report = await kpi_service.get_manual_movements_rate(filial_id, date.today(), date.today())
    assert report.total_count == 2
    assert report.manual_count == 1
    assert report.rate == 0.5


# --- Structural: no PATCH/DELETE route exists for income/expense entries ---


def test_no_edit_or_delete_route_for_manual_entries():
    for route in administracion_router.routes:
        if "/income-entries" in route.path or "/expense-entries" in route.path:
            assert "PATCH" not in route.methods
            assert "DELETE" not in route.methods
