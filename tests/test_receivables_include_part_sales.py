"""Cuentas por Cobrar must include every uncollected sale, not just ODS
invoices — a parts counter sale never posts any income until it reaches
COMPLETADO (PartsService.update_sale_status), so anything short of that is,
by the system's own definition, still outstanding. This is also what makes
CxC + ingresos reconcile exactly against Rentabilidad's net_sales."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.administracion.service import AdministracionService
from app.modules.filiales.models import Filial
from app.modules.parts.enums import PartSaleStatus
from app.modules.parts.models import Part, PartSale, PartSaleLine


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


def make_part_sale(session, filial_id, sequence_number, status, line_total, client_name="Cliente", created_at=None):
    part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code=f"P-{sequence_number}", name="Filtro", price=10)
    session.add(part)
    session.commit()
    sale = PartSale(
        filial_id=filial_id, sequence_number=sequence_number, client_name=client_name, status=status,
        created_at=created_at or datetime.now(timezone.utc),
    )
    session.add(sale)
    session.commit()
    session.add(
        PartSaleLine(
            part_sale_id=sale.id, part_id=part.id, quantity=1, unit_price=line_total, unit_cost=line_total * 0.7,
            line_total=line_total,
        )
    )
    session.commit()
    return sale


@pytest.mark.asyncio
async def test_uncollected_part_sales_appear_in_receivables_with_aging(env):
    admin, session, filial_id = env
    now = datetime.now(timezone.utc)
    vr_5001 = make_part_sale(session, filial_id, 5001, PartSaleStatus.PENDIENTE, 150.0, created_at=now - timedelta(days=5))
    vr_5002 = make_part_sale(session, filial_id, 5002, PartSaleStatus.PEDIDO, 240.0, created_at=now - timedelta(days=40))

    receivables = await admin.list_receivables(filial_id)
    part_receivables = {r.code: r for r in receivables if r.document_type == "part_sale"}

    assert set(part_receivables) == {"VR-5001", "VR-5002"}
    assert part_receivables["VR-5001"].pending_amount == 150.0
    assert part_receivables["VR-5002"].pending_amount == 240.0
    assert sum(r.pending_amount for r in part_receivables.values()) == 390.0
    assert part_receivables["VR-5001"].aging_bucket == "0-30"
    assert part_receivables["VR-5002"].aging_bucket == "31-60"
    assert part_receivables["VR-5001"].invoice_id == vr_5001.id
    assert part_receivables["VR-5002"].invoice_id == vr_5002.id


@pytest.mark.asyncio
async def test_completed_and_cancelled_part_sales_are_excluded(env):
    admin, session, filial_id = env
    make_part_sale(session, filial_id, 5001, PartSaleStatus.COMPLETADO, 150.0)
    make_part_sale(session, filial_id, 5002, PartSaleStatus.CANCELADO, 240.0)

    receivables = await admin.list_receivables(filial_id)
    assert [r for r in receivables if r.document_type == "part_sale"] == []


@pytest.mark.asyncio
async def test_receivables_plus_income_reconciles_with_rentabilidad_net_sales(env):
    admin, session, filial_id = env
    now = datetime.now(timezone.utc)
    make_part_sale(session, filial_id, 5001, PartSaleStatus.PENDIENTE, 150.0, created_at=now)
    make_part_sale(session, filial_id, 5002, PartSaleStatus.PEDIDO, 240.0, created_at=now)
    completed = make_part_sale(session, filial_id, 5003, PartSaleStatus.COMPLETADO, 100.0, created_at=now)
    # A completado sale's income is posted separately by PartsService at the
    # moment of transition (update_sale_status) — simulate that here since
    # this test only constructs rows directly.
    from app.modules.administracion.enums import AccountCurrency, AccountType, IncomeSource, MovementSourceType
    from app.modules.administracion.models import Account, IncomeEntry
    from app.core.venezuela_time import venezuela_today

    account = Account(filial_id=filial_id, name="Caja", currency=AccountCurrency.USD, account_type=AccountType.CAJA)
    session.add(account)
    session.commit()
    session.add(
        IncomeEntry(
            filial_id=filial_id, entry_date=venezuela_today(), source=IncomeSource.AUTOMATICO,
            origin_reference=completed.code, description="Cierre de venta de repuestos", amount=100.0,
            currency=AccountCurrency.USD, account_id=account.id, source_type=MovementSourceType.PART_SALE,
            source_id=completed.id,
        )
    )
    session.commit()

    receivables = await admin.list_receivables(filial_id)
    cxc_total = sum(r.pending_amount for r in receivables if r.document_type == "part_sale")

    report = await admin.get_profitability(filial_id, date.today() - timedelta(days=1), date.today() + timedelta(days=1))
    repuestos = next(d for d in report.departments if d.key == "repuestos")

    assert cxc_total == 390.0
    assert repuestos.net_sales == 490.0  # 150 + 240 + 100, every non-cancelled sale
    income_from_parts = 100.0  # only the completado sale posted income
    assert cxc_total + income_from_parts == repuestos.net_sales
