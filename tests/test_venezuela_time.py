"""Income/expense entries and invoice billing days must all use Venezuela's
calendar day (America/Caracas, UTC-4, no DST), not whatever timezone the
server process happens to run in — otherwise a sale closed late in the
Venezuela evening can get stamped with tomorrow's UTC date, and daily income
totals stop reconciling with daily sales totals."""

import uuid
from datetime import date, datetime, timezone
from datetime import datetime as real_datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
import app.core.venezuela_time as vt
import app.modules.administracion.service as admin_service_module
from app.core.database import Base
from app.modules.administracion.enums import AccountCurrency, AccountType
from app.modules.administracion.schemas import AccountCreate
from app.modules.administracion.service import AdministracionService
from app.modules.filiales.models import Filial


class _FrozenDateTime(real_datetime):
    """A sale closing at 22:00 VET on Sept 17 is 02:00 UTC on Sept 18 — a
    server running in UTC calling bare date.today() would misfile it under
    the 18th."""

    _instant_utc = real_datetime(2026, 9, 18, 2, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._instant_utc if tz is None else cls._instant_utc.astimezone(tz)


def test_venezuela_today_uses_caracas_calendar_day_not_utc(monkeypatch):
    monkeypatch.setattr(vt, "datetime", _FrozenDateTime)
    assert vt.venezuela_today() == date(2026, 9, 17)


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        session.commit()
        db = AsyncAdapter(session)
        yield AdministracionService(db), filial_id


@pytest.mark.asyncio
async def test_record_automatic_income_dates_itself_in_venezuela_time(env, monkeypatch):
    service, filial_id = env
    monkeypatch.setattr(admin_service_module, "venezuela_today", lambda: date(2026, 9, 17))
    account = await service.create_account(
        AccountCreate(filial_id=filial_id, name="Caja", currency=AccountCurrency.USD, account_type=AccountType.CAJA)
    )
    entry = await service.record_automatic_income(filial_id, "Venta de vehículo", 100, "CV-1")
    assert entry.entry_date == date(2026, 9, 17)
    assert account.id == entry.account_id
