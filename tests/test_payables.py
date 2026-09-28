"""Cuentas por Pagar — a conciliada PurchaseRequest is a debt to its
supplier until an egreso (categoría compras_proveedores) links against it.
Computed the same way as Cuentas por Cobrar: no persisted "payable" row,
just PurchaseRequest.paid_at IS NULL."""

import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.administracion.enums import (
    AccountCurrency,
    AccountType,
    CounterpartyType,
    ExpenseCategory,
    PurchaseRequestStatus,
    SupplierStatus,
    SupplierType,
)
from app.modules.administracion.exceptions import (
    PurchaseRequestNotPayableError,
    PurchaseRequestRequiresSupplierCounterpartyError,
    PurchaseRequestSupplierMismatchError,
)
from app.modules.administracion.models import Account, PurchaseRequest, PurchaseRequestLine, Supplier
from app.modules.administracion.schemas import ExpenseEntryCreate
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
        account = Account(filial_id=filial_id, name="Caja USD", currency=AccountCurrency.USD, account_type=AccountType.CAJA)
        supplier = Supplier(
            filial_id=filial_id, business_name="Mitsubishi Vzla", rif="J-11111111-1",
            supplier_type=SupplierType.IMPORTADOR, status=SupplierStatus.ACTIVO,
        )
        other_supplier = Supplier(
            filial_id=filial_id, business_name="Otro Proveedor", rif="J-22222222-2",
            supplier_type=SupplierType.NACIONAL, status=SupplierStatus.ACTIVO,
        )
        session.add_all([account, supplier, other_supplier])
        session.commit()

        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Filtro", price=10)
        session.add(part)
        session.commit()

        db = AsyncAdapter(session)
        yield AdministracionService(db), session, filial_id, account, supplier, other_supplier, part


def _make_request(session, filial_id, supplier_id, part_id, sequence_number, *, quantity=1, unit_cost=120):
    request = PurchaseRequest(
        filial_id=filial_id, sequence_number=sequence_number, supplier_id=supplier_id,
        status=PurchaseRequestStatus.CONCILIADA,
    )
    session.add(request)
    session.commit()
    session.add(
        PurchaseRequestLine(purchase_request_id=request.id, part_id=part_id, quantity=quantity, unit_cost=unit_cost)
    )
    session.commit()
    return request


def _expense_payload(filial_id, account_id, supplier_id, purchase_request_ids, **overrides):
    data = dict(
        filial_id=filial_id,
        entry_date=date.today(),
        category=ExpenseCategory.COMPRAS_PROVEEDORES,
        beneficiary="Mitsubishi Vzla",
        description="Pago SC-1001",
        amount=90,
        currency=AccountCurrency.USD,
        account_id=account_id,
        counterparty_type=CounterpartyType.PROVEEDOR,
        counterparty_supplier_id=supplier_id,
        purchase_request_ids=purchase_request_ids,
    )
    data.update(overrides)
    return ExpenseEntryCreate(**data)


@pytest.mark.asyncio
async def test_conciliada_request_appears_as_a_payable(env):
    service, session, filial_id, _account, supplier, _other, part = env
    request = _make_request(session, filial_id, supplier.id, part.id, sequence_number=1001, quantity=1, unit_cost=120)

    payables = await service.list_payables(filial_id)

    assert len(payables) == 1
    assert payables[0].purchase_request_id == request.id
    assert payables[0].code == "SC-1001"
    assert payables[0].supplier_name == "Mitsubishi Vzla"
    assert payables[0].total_amount == 120.0


@pytest.mark.asyncio
async def test_paying_it_removes_it_from_payables(env):
    service, session, filial_id, account, supplier, _other, part = env
    request = _make_request(session, filial_id, supplier.id, part.id, sequence_number=1001)

    payload = _expense_payload(filial_id, account.id, supplier.id, [request.id])
    await service.create_expense(payload, None, uuid.uuid4())

    payables = await service.list_payables(filial_id)
    assert payables == []

    session.refresh(request)
    assert request.paid_at is not None
    assert request.payment_account_id == account.id


@pytest.mark.asyncio
async def test_linking_a_request_from_another_supplier_is_rejected(env):
    service, session, filial_id, account, supplier, other_supplier, part = env
    request = _make_request(session, filial_id, other_supplier.id, part.id, sequence_number=1002)

    payload = _expense_payload(filial_id, account.id, supplier.id, [request.id])
    with pytest.raises(PurchaseRequestSupplierMismatchError):
        await service.create_expense(payload, None, uuid.uuid4())


@pytest.mark.asyncio
async def test_linking_an_already_paid_request_is_rejected(env):
    service, session, filial_id, account, supplier, _other, part = env
    request = _make_request(session, filial_id, supplier.id, part.id, sequence_number=1003)
    payload = _expense_payload(filial_id, account.id, supplier.id, [request.id])
    await service.create_expense(payload, None, uuid.uuid4())

    with pytest.raises(PurchaseRequestNotPayableError):
        await service.create_expense(
            _expense_payload(filial_id, account.id, supplier.id, [request.id]), None, uuid.uuid4()
        )


@pytest.mark.asyncio
async def test_linking_requires_a_supplier_counterparty(env):
    service, session, filial_id, account, supplier, _other, part = env
    request = _make_request(session, filial_id, supplier.id, part.id, sequence_number=1004)

    payload = _expense_payload(
        filial_id, account.id, supplier.id, [request.id],
        counterparty_type=CounterpartyType.TERCERO, counterparty_supplier_id=None, counterparty_name="Alguien",
    )
    with pytest.raises(PurchaseRequestRequiresSupplierCounterpartyError):
        await service.create_expense(payload, None, uuid.uuid4())


@pytest.mark.asyncio
async def test_linked_expense_still_excluded_from_operating_expenses(env):
    """Regression: compras_proveedores must keep being excluded from
    'gastos operacionales' in Rentabilidad even when linked to a PO —
    it's already counted via parts FIFO cost."""
    service, session, filial_id, account, supplier, _other, part = env
    request = _make_request(session, filial_id, supplier.id, part.id, sequence_number=1005)
    payload = _expense_payload(filial_id, account.id, supplier.id, [request.id])
    await service.create_expense(payload, None, uuid.uuid4())

    report = await service.get_profitability(filial_id, date.today().replace(day=1), date.today())
    adjustment = next(a for a in report.adjustments if a.key == "gastos_operacionales")
    assert adjustment.amount == 0.0


@pytest.mark.asyncio
async def test_multiple_purchase_orders_can_be_paid_in_one_expense(env):
    service, session, filial_id, account, supplier, _other, part = env
    request_a = _make_request(session, filial_id, supplier.id, part.id, sequence_number=1006, unit_cost=40)
    request_b = _make_request(session, filial_id, supplier.id, part.id, sequence_number=1007, unit_cost=45)

    payload = _expense_payload(filial_id, account.id, supplier.id, [request_a.id, request_b.id], amount=85)
    await service.create_expense(payload, None, uuid.uuid4())

    assert await service.list_payables(filial_id) == []
    session.refresh(request_a)
    session.refresh(request_b)
    assert request_a.paid_at is not None
    assert request_b.paid_at is not None
