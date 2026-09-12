"""Regression tests for resolving a supplier claim once the importador rejects it:
either charge the client (decision-only, no automatic money movement) or absorb
it as a workshop cost (posts a real ExpenseEntry)."""

import os
import uuid

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exceptions import BadRequestError
from app.modules.administracion.enums import (
    AccountCurrency,
    AccountType,
    ClaimResolution,
    ClaimStatus,
    ExpenseCategory,
    SupplierStatus,
    SupplierType,
)
from app.modules.administracion.models import Account, ExpenseEntry, Supplier, SupplierClaim
from app.modules.administracion.schemas import SupplierClaimResolveInput, SupplierClaimUpdate
from app.modules.administracion.service import AdministracionService
from app.modules.clients.enums import ClientType, ContactPreference, DocumentType, AddressType
from app.modules.clients.models import Client
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

        part = Part(filial_id=filial_id, code="P-1", name="Alternador", price=100)
        session.add(part)

        client = Client(
            filial_id=filial_id,
            full_name="Juan Pérez",
            client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V,
            document_number="12345678",
            phone_primary="04140000000",
            address="Caracas",
        )
        session.add(client)

        account = Account(
            filial_id=filial_id, name="Caja principal", currency=AccountCurrency.USD, account_type=AccountType.CAJA
        )
        session.add(account)
        session.commit()

        claim = SupplierClaim(
            filial_id=filial_id,
            part_id=part.id,
            quantity=1,
            supplier_id=supplier.id,
            status=ClaimStatus.RECHAZADO,
            claimed_amount=150,
            currency=AccountCurrency.USD,
        )
        session.add(claim)
        session.commit()

        db = AsyncAdapter(session)
        yield AdministracionService(db), session, claim.id, client.id, account.id


@pytest.mark.asyncio
async def test_absorb_as_workshop_cost_posts_a_real_expense(env):
    service, session, claim_id, _client_id, account_id = env
    resolved_by = uuid.uuid4()

    updated = await service.resolve_claim(
        claim_id,
        SupplierClaimResolveInput(resolution=ClaimResolution.COSTO_TALLER, account_id=account_id, note="Sin cobertura"),
        resolved_by,
    )

    assert updated.status == ClaimStatus.RESUELTO
    assert updated.resolution == ClaimResolution.COSTO_TALLER
    assert updated.resolved_by_user_id == resolved_by
    assert updated.resolved_at is not None
    assert updated.expense_entry_id is not None

    expense = session.get(ExpenseEntry, updated.expense_entry_id)
    assert expense is not None
    assert float(expense.amount) == 150
    assert expense.category == ExpenseCategory.GARANTIA_RECHAZADA
    assert expense.account_id == account_id


@pytest.mark.asyncio
async def test_charge_to_client_only_records_the_decision(env):
    service, session, claim_id, client_id, _account_id = env

    updated = await service.resolve_claim(
        claim_id,
        SupplierClaimResolveInput(resolution=ClaimResolution.CARGO_CLIENTE, client_id=client_id),
        uuid.uuid4(),
    )

    assert updated.status == ClaimStatus.RESUELTO
    assert updated.resolution == ClaimResolution.CARGO_CLIENTE
    assert updated.client_id == client_id
    assert updated.expense_entry_id is None
    assert session.query(ExpenseEntry).count() == 0


@pytest.mark.asyncio
async def test_charge_to_client_requires_a_client(env):
    service, _session, claim_id, _client_id, _account_id = env

    with pytest.raises(BadRequestError):
        await service.resolve_claim(
            claim_id, SupplierClaimResolveInput(resolution=ClaimResolution.CARGO_CLIENTE), uuid.uuid4()
        )


@pytest.mark.asyncio
async def test_workshop_cost_requires_matching_account_currency(env):
    service, session, claim_id, _client_id, _account_id = env
    bs_account = Account(
        filial_id=session.get(SupplierClaim, claim_id).filial_id,
        name="Cuenta Bs",
        currency=AccountCurrency.BS,
        account_type=AccountType.CORRIENTE,
    )
    session.add(bs_account)
    session.commit()

    with pytest.raises(BadRequestError):
        await service.resolve_claim(
            claim_id,
            SupplierClaimResolveInput(resolution=ClaimResolution.COSTO_TALLER, account_id=bs_account.id),
            uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_cannot_resolve_a_claim_that_is_not_rejected(env):
    service, session, claim_id, _client_id, account_id = env
    claim = session.get(SupplierClaim, claim_id)
    claim.status = ClaimStatus.APROBADO
    session.commit()

    with pytest.raises(BadRequestError):
        await service.resolve_claim(
            claim_id,
            SupplierClaimResolveInput(resolution=ClaimResolution.COSTO_TALLER, account_id=account_id),
            uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_cannot_patch_status_straight_to_resuelto(env):
    service, _session, claim_id, _client_id, _account_id = env

    with pytest.raises(BadRequestError):
        await service.update_claim(claim_id, SupplierClaimUpdate(status=ClaimStatus.RESUELTO))


@pytest.mark.asyncio
async def test_cannot_resolve_a_claim_without_an_amount(env):
    service, session, claim_id, _client_id, account_id = env
    claim = session.get(SupplierClaim, claim_id)
    claim.claimed_amount = None
    session.commit()

    with pytest.raises(BadRequestError):
        await service.resolve_claim(
            claim_id,
            SupplierClaimResolveInput(resolution=ClaimResolution.COSTO_TALLER, account_id=account_id),
            uuid.uuid4(),
        )
