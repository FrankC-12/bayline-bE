"""Regression tests for auto-syncing labor_settings.bcv_rate from the
exchange_rates scraper, the manual-override path, and the >0 validation."""

import os
import uuid
from datetime import date, timedelta

os.environ["DEBUG"] = "false"

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.administracion.service import AdministracionService
from app.modules.exchange_rates.models import ExchangeRate
from app.modules.filiales.models import Filial
from app.modules.post_ventas.schemas import LaborSettingsUpdate
from app.modules.post_ventas.service import PostVentasService

VALID_UPDATE = dict(hourly_rate=25, commission_percentage=30, igtf_percentage=3, iva_percentage=16)


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        session.commit()
        db = AsyncAdapter(session)
        yield PostVentasService(db), db, session, filial_id


@pytest.mark.asyncio
async def test_no_scraped_rate_stays_zero_and_stale(env):
    service, _db, _session, filial_id = env
    settings = await service.get_labor_settings(filial_id)
    assert float(settings.bcv_rate) == 0
    assert settings.bcv_rate_is_stale is True


@pytest.mark.asyncio
async def test_syncs_from_latest_scraped_usd_rate(env):
    service, _db, session, filial_id = env
    session.add(ExchangeRate(currency="USD", rate_ves=100, value_date=date.today()))
    session.commit()

    settings = await service.get_labor_settings(filial_id)

    assert float(settings.bcv_rate) == 100
    assert settings.bcv_rate_date == date.today()
    assert settings.bcv_rate_is_stale is False


@pytest.mark.asyncio
async def test_older_scraped_rate_does_not_overwrite_fresher_manual_value(env):
    service, _db, session, filial_id = env
    await service.update_labor_settings(
        filial_id, LaborSettingsUpdate(**VALID_UPDATE, bcv_rate=250)
    )
    session.add(
        ExchangeRate(currency="USD", rate_ves=100, value_date=date.today() - timedelta(days=1))
    )
    session.commit()

    settings = await service.get_labor_settings(filial_id)

    assert float(settings.bcv_rate) == 250
    assert settings.bcv_rate_is_stale is False


@pytest.mark.asyncio
async def test_update_without_bcv_rate_keeps_the_synced_value(env):
    service, _db, session, filial_id = env
    session.add(ExchangeRate(currency="USD", rate_ves=100, value_date=date.today()))
    session.commit()
    await service.get_labor_settings(filial_id)

    settings = await service.update_labor_settings(
        filial_id, LaborSettingsUpdate(**{**VALID_UPDATE, "hourly_rate": 30}, bcv_rate=None)
    )

    assert float(settings.bcv_rate) == 100
    assert settings.hourly_rate == 30


def test_bcv_rate_of_zero_is_rejected():
    with pytest.raises(ValidationError):
        LaborSettingsUpdate(**VALID_UPDATE, bcv_rate=0)


@pytest.mark.asyncio
async def test_administracion_reuses_the_same_synced_rate(env):
    service, db, session, filial_id = env
    session.add(ExchangeRate(currency="USD", rate_ves=100, value_date=date.today()))
    session.commit()

    settings = await service.get_labor_settings(filial_id)
    administracion_rate = await AdministracionService(db)._get_bcv_rate(filial_id)

    assert administracion_rate == float(settings.bcv_rate)
