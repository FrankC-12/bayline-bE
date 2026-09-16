"""Regression tests for the monthly warranty submission (presentación) sent to
the holding: it bundles SupplierClaims resolved as COSTO_TALLER within a
period, moves borrador -> presentada -> pagada, and posts a real IncomeEntry
once paid."""

import os
import uuid
from datetime import date, datetime, timezone

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exceptions import BadRequestError, NotFoundError
from app.modules.administracion.enums import (
    AccountCurrency,
    AccountType,
    ClaimResolution,
    ClaimStatus,
    SupplierStatus,
    SupplierType,
    WarrantySubmissionStatus,
)
from app.modules.administracion.models import Account, IncomeEntry, Supplier, SupplierClaim, WarrantySubmission
from app.modules.administracion.schemas import WarrantySubmissionCreate, WarrantySubmissionPayInput
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

        supplier = Supplier(
            filial_id=filial_id,
            business_name="Importadora XYZ",
            rif="J-12345678-9",
            supplier_type=SupplierType.IMPORTADOR,
            status=SupplierStatus.ACTIVO,
        )
        session.add(supplier)

        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Alternador", price=100)
        session.add(part)

        account = Account(
            filial_id=filial_id, name="Caja principal", currency=AccountCurrency.USD, account_type=AccountType.CAJA
        )
        session.add(account)
        session.commit()

        def make_claim(resolved_at: datetime, amount: float = 100) -> SupplierClaim:
            claim = SupplierClaim(
                filial_id=filial_id,
                part_id=part.id,
                quantity=1,
                supplier_id=supplier.id,
                status=ClaimStatus.RESUELTO,
                claimed_amount=amount,
                currency=AccountCurrency.USD,
                resolution=ClaimResolution.COSTO_TALLER,
                resolved_at=resolved_at,
            )
            session.add(claim)
            session.commit()
            return claim

        db = AsyncAdapter(session)
        yield AdministracionService(db), session, filial_id, account.id, make_claim


@pytest.mark.asyncio
async def test_create_submission_bundles_only_matching_period_and_currency(env):
    service, _session, filial_id, _account_id, make_claim = env
    in_period = make_claim(datetime(2026, 3, 15, tzinfo=timezone.utc))
    before_period = make_claim(datetime(2026, 2, 28, tzinfo=timezone.utc))
    after_period = make_claim(datetime(2026, 4, 1, tzinfo=timezone.utc))

    submission = await service.create_warranty_submission(
        WarrantySubmissionCreate(filial_id=filial_id, period_year=2026, period_month=3, currency=AccountCurrency.USD)
    )

    claim_ids = {c.id for c in submission.claims}
    assert claim_ids == {in_period.id}
    assert before_period.id not in claim_ids
    assert after_period.id not in claim_ids
    assert submission.status == WarrantySubmissionStatus.BORRADOR
    assert submission.total_claimed_amount == 100
    assert submission.code.startswith("PG-")


@pytest.mark.asyncio
async def test_cannot_create_a_second_submission_for_the_same_period(env):
    service, _session, filial_id, _account_id, _make_claim = env
    await service.create_warranty_submission(
        WarrantySubmissionCreate(filial_id=filial_id, period_year=2026, period_month=3, currency=AccountCurrency.USD)
    )

    with pytest.raises(BadRequestError):
        await service.create_warranty_submission(
            WarrantySubmissionCreate(filial_id=filial_id, period_year=2026, period_month=3, currency=AccountCurrency.USD)
        )


@pytest.mark.asyncio
async def test_refresh_picks_up_claims_resolved_after_the_draft_was_created(env):
    service, _session, filial_id, _account_id, make_claim = env
    submission = await service.create_warranty_submission(
        WarrantySubmissionCreate(filial_id=filial_id, period_year=2026, period_month=3, currency=AccountCurrency.USD)
    )
    assert submission.claims == []

    late_claim = make_claim(datetime(2026, 3, 20, tzinfo=timezone.utc))
    refreshed = await service.refresh_warranty_submission(submission.id)

    assert {c.id for c in refreshed.claims} == {late_claim.id}


@pytest.mark.asyncio
async def test_cannot_submit_an_empty_draft(env):
    service, _session, filial_id, _account_id, _make_claim = env
    submission = await service.create_warranty_submission(
        WarrantySubmissionCreate(filial_id=filial_id, period_year=2026, period_month=3, currency=AccountCurrency.USD)
    )

    with pytest.raises(BadRequestError):
        await service.submit_warranty_submission(submission.id, uuid.uuid4())


@pytest.mark.asyncio
async def test_full_lifecycle_submit_then_pay_posts_income(env):
    service, session, filial_id, account_id, make_claim = env
    make_claim(datetime(2026, 3, 5, tzinfo=timezone.utc), amount=200)
    submission = await service.create_warranty_submission(
        WarrantySubmissionCreate(filial_id=filial_id, period_year=2026, period_month=3, currency=AccountCurrency.USD)
    )

    submitted = await service.submit_warranty_submission(submission.id, uuid.uuid4())
    assert submitted.status == WarrantySubmissionStatus.PRESENTADA
    assert submitted.submitted_at is not None

    paid_by = uuid.uuid4()
    paid = await service.mark_warranty_submission_paid(
        submission.id,
        WarrantySubmissionPayInput(account_id=account_id, withholding_amount=20, net_amount_received=180),
        paid_by,
    )

    assert paid.status == WarrantySubmissionStatus.PAGADA
    assert paid.withholding_amount == 20
    assert paid.net_amount_received == 180
    assert paid.paid_by_user_id == paid_by
    assert paid.income_entry_id is not None

    income = session.get(IncomeEntry, paid.income_entry_id)
    assert income is not None
    assert float(income.amount) == 180
    assert income.account_id == account_id


@pytest.mark.asyncio
async def test_cannot_refresh_or_submit_once_submitted(env):
    service, _session, filial_id, _account_id, make_claim = env
    make_claim(datetime(2026, 3, 5, tzinfo=timezone.utc))
    submission = await service.create_warranty_submission(
        WarrantySubmissionCreate(filial_id=filial_id, period_year=2026, period_month=3, currency=AccountCurrency.USD)
    )
    await service.submit_warranty_submission(submission.id, uuid.uuid4())

    with pytest.raises(BadRequestError):
        await service.refresh_warranty_submission(submission.id)
    with pytest.raises(BadRequestError):
        await service.submit_warranty_submission(submission.id, uuid.uuid4())


@pytest.mark.asyncio
async def test_cannot_pay_a_submission_still_in_draft(env):
    service, _session, filial_id, account_id, make_claim = env
    make_claim(datetime(2026, 3, 5, tzinfo=timezone.utc))
    submission = await service.create_warranty_submission(
        WarrantySubmissionCreate(filial_id=filial_id, period_year=2026, period_month=3, currency=AccountCurrency.USD)
    )

    with pytest.raises(BadRequestError):
        await service.mark_warranty_submission_paid(
            submission.id,
            WarrantySubmissionPayInput(account_id=account_id, withholding_amount=0, net_amount_received=100),
            uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_deleting_a_draft_frees_its_claims(env):
    service, session, filial_id, _account_id, make_claim = env
    claim = make_claim(datetime(2026, 3, 5, tzinfo=timezone.utc))
    submission = await service.create_warranty_submission(
        WarrantySubmissionCreate(filial_id=filial_id, period_year=2026, period_month=3, currency=AccountCurrency.USD)
    )

    await service.delete_warranty_submission(submission.id)

    session.expire_all()
    refreshed_claim = session.get(SupplierClaim, claim.id)
    assert refreshed_claim.warranty_submission_id is None
    assert session.get(WarrantySubmission, submission.id) is None


@pytest.mark.asyncio
async def test_export_csv_includes_the_bundled_claims(env):
    service, _session, filial_id, _account_id, make_claim = env
    make_claim(datetime(2026, 3, 5, tzinfo=timezone.utc), amount=150)
    submission = await service.create_warranty_submission(
        WarrantySubmissionCreate(filial_id=filial_id, period_year=2026, period_month=3, currency=AccountCurrency.USD)
    )

    content, filename = await service.export_warranty_submission_csv(submission.id)

    assert filename == f"{submission.code}.csv"
    assert "Alternador" in content
    assert "150" in content
