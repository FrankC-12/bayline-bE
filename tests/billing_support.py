import uuid
from decimal import Decimal

from sqlalchemy import select

from app.modules.administracion.enums import AccountCurrency, AccountType
from app.modules.administracion.models import Account
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.exchange_rates.models import ExchangeRate
from app.modules.filiales.models import Filial
from app.modules.post_ventas.models import LaborSettings
from app.modules.service_orders.billing import BillingService, billing_day
from app.modules.service_orders.billing_schemas import BillingInput, InvoiceCreate


def configure_billing(service, session, order, igtf=3):
    client = Client(
        id=uuid.uuid4(),
        filial_id=order.filial_id,
        full_name="Cliente <prueba>",
        client_type=ClientType.PARTICULAR,
        document_type=DocumentType.V,
        document_number="12345678",
        phone_primary="04121234567",
        address="Caracas",
    )
    session.add_all(
        [
            client,
            Filial(id=order.filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"),
            Vehicle(
                id=order.vehicle_id,
                client_id=client.id,
                brand="Toyota",
                model="Corolla",
                plate="ABC123",
            ),
            ExchangeRate(currency="USD", rate_ves=50, value_date=billing_day()),
        ]
    )
    settings = session.scalar(
        select(LaborSettings).where(LaborSettings.filial_id == order.filial_id)
    )
    if settings is None:
        settings = LaborSettings(filial_id=order.filial_id, hourly_rate=25, iva_percentage=16)
        session.add(settings)
    settings.igtf_percentage = igtf
    accounts = [
        Account(
            filial_id=order.filial_id,
            name=currency.value,
            currency=currency,
            account_type=AccountType.CAJA,
        )
        for currency in [AccountCurrency.USD, AccountCurrency.BS]
    ]
    session.add_all(accounts)
    session.commit()
    return BillingService(service.db), accounts, settings


async def invoice_payload(billing, order, accounts, method="usd", usd_base="0"):
    quote = await billing.quote(order.id, BillingInput(payment_method=method, usd_base=usd_base))
    return InvoiceCreate(
        payment_method=method,
        usd_base=usd_base,
        request_id=uuid.uuid4(),
        quote_hash=quote.quote_hash,
        paid_usd=Decimal(str(quote.due_usd)),
        paid_bs=Decimal(str(quote.due_bs)),
        usd_account_id=accounts[0].id,
        bs_account_id=accounts[1].id,
        payment_reference="REF-001",
    )
