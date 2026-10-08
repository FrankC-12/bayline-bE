"""Real async sessions against the explicitly configured disposable test database."""

import os
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.core.models_registry  # noqa: F401
from app.modules.administracion.enums import AccountCurrency, AccountType
from app.modules.administracion.models import Account
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.exchange_rates.models import ExchangeRate
from app.modules.filiales.models import Filial
from app.modules.holdings.models import Holding
from app.modules.post_ventas.enums import TemparioCategory
from app.modules.post_ventas.models import LaborSettings, Tempario
from app.modules.service_orders.billing import BillingService, billing_day
from app.modules.service_orders.billing_schemas import BillingInput, InvoiceCreate
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.models import (
    ServiceOrder,
    ServiceOrderTask,
    ServiceOrderTypeCatalog,
)


@pytest_asyncio.fixture
async def billing_db():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Run scripts/test_postgres.py to enable real PostgreSQL integration tests.")
    parsed = make_url(url)
    if parsed.drivername != "postgresql+asyncpg" or parsed.database != "bayline_test":
        pytest.fail(
            "Integration tests require an explicitly named bayline_test PostgreSQL database."
        )
    engine = create_async_engine(
        url, connect_args={"server_settings": {"statement_timeout": "10000"}}
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    token = uuid.uuid4().hex
    async with sessions() as db:
        holding = Holding(name="Test holding", slug=token)
        db.add(holding)
        await db.flush()
        filial = Filial(holding_id=holding.id, name="Test filial", slug=token)
        db.add(filial)
        await db.flush()
        client = Client(
            filial_id=filial.id,
            full_name="Cliente integración",
            client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V,
            document_number="12345678",
            phone_primary="04121234567",
            address="Caracas",
        )
        db.add(client)
        await db.flush()
        vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Hilux", plate="AB123CD")
        kind = ServiceOrderTypeCatalog(
            filial_id=filial.id,
            code="regular",
            name="Regular",
            is_system=True,
            is_selectable=True,
            is_active=True,
        )
        tempario = Tempario(
            filial_id=filial.id,
            category=TemparioCategory.MOTOR,
            sequence_number=1,
            name="Servicio de motor",
            estimated_hours=2,
        )
        settings = LaborSettings(
            filial_id=filial.id, hourly_rate=25, iva_percentage=16, igtf_percentage=3
        )
        accounts = [
            Account(
                filial_id=filial.id,
                name=currency.value,
                currency=currency,
                account_type=AccountType.CAJA,
            )
            for currency in (AccountCurrency.USD, AccountCurrency.BS)
        ]
        db.add_all([vehicle, kind, tempario, settings, *accounts])
        await db.flush()
        order = ServiceOrder(
            filial_id=filial.id,
            vehicle_id=vehicle.id,
            order_type_id=kind.id,
            sequence_number=1,
            status=ServiceOrderStatus.COMPLETADO,
        )
        db.add(order)
        await db.flush()
        db.add(
            ServiceOrderTask(
                service_order_id=order.id,
                tempario_id=tempario.id,
                code_snapshot=tempario.code,
                name_snapshot=tempario.name,
                hours_snapshot=2,
            )
        )
        rate = (
            await db.execute(
                select(ExchangeRate).where(
                    ExchangeRate.currency == "USD", ExchangeRate.value_date == billing_day()
                )
            )
        ).scalar_one_or_none()
        if rate is None:
            db.add(ExchangeRate(currency="USD", value_date=billing_day(), rate_ves=50))
        await db.commit()
        context = SimpleNamespace(
            sessions=sessions,
            order_id=order.id,
            filial_id=filial.id,
            usd_account_id=accounts[0].id,
            bs_account_id=accounts[1].id,
        )
    try:
        yield context
    finally:
        await engine.dispose()


async def invoice_input(ctx, *, method="usd", paid=True):
    async with ctx.sessions() as db:
        quote = await BillingService(db).quote(
            ctx.order_id,
            BillingInput(payment_method=method, usd_base="20" if method == "mixed" else "0"),
        )
    return InvoiceCreate(
        payment_method=method,
        usd_base="20" if method == "mixed" else "0",
        request_id=uuid.uuid4(),
        quote_hash=quote.quote_hash,
        paid_usd=str(quote.due_usd) if paid else "0",
        paid_bs=str(quote.due_bs) if paid else "0",
        usd_account_id=ctx.usd_account_id,
        bs_account_id=ctx.bs_account_id,
        payment_reference="Integración",
    )
