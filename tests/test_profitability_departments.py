"""Departmentalized Rentabilidad: revenue+cost split by department instead of
one blended margin, revenue recognized at invoice issuance (not collection),
the two new manual income concepts (garantía de marca / F&I), diferencia en
cambio, the period-lock display, the historical BCV rate, and the holding-
wide consolidated view."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.administracion.enums import AccountCurrency, AccountType, CounterpartyType, IncomeConcept, IncomeSource
from app.modules.administracion.models import IncomeEntry
from app.modules.administracion.schemas import AccountCreate, IncomeEntryCreate
from app.modules.administracion.service import AdministracionService
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.concesionario.enums import SaleType, VehicleCondition, VehicleStatus
from app.modules.concesionario.models import DealershipVehicle, VehicleSale
from app.modules.exchange_rates.models import ExchangeRate
from app.modules.filiales.models import Filial
from app.modules.parts.enums import PartSaleStatus
from app.modules.parts.models import Part, PartSale, PartSaleLine
from app.modules.service_orders.enums import TransferStatus
from app.modules.service_orders.models import (
    ServiceOrder,
    ServiceOrderInvoice,
    ServiceOrderTransfer,
    ServiceOrderTransferLine,
    ServiceOrderTransferLotAllocation,
)
from app.modules.service_orders.service import ServiceOrderService
from app.modules.warehouse.models import PartLot, Warehouse


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        holding_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=holding_id, name="Taller", slug="taller"))
        client = Client(
            filial_id=filial_id, full_name="Cliente", client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V, document_number="12345678", phone_primary="04121234567", address="Caracas",
        )
        session.add(client)
        session.commit()
        db = AsyncAdapter(session)
        yield AdministracionService(db), session, filial_id, holding_id, client


def make_vehicle_sale(session, filial_id, condition, cost_price, cost_is_estimated, final_price, created_at):
    vehicle = DealershipVehicle(
        filial_id=filial_id, status=VehicleStatus.VENDIDO, condition=condition, brand="Toyota", model="Corolla",
        year=2024, vin=str(uuid.uuid4())[:17], sku=f"SKU-{uuid.uuid4().hex[:8]}", price_cash=final_price,
        price_financed=final_price, cost_price=cost_price, cost_is_estimated=cost_is_estimated,
    )
    session.add(vehicle)
    session.commit()
    sale = VehicleSale(
        filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id, client_name="Cliente",
        sale_type=SaleType.CONTADO, final_price=final_price, created_at=created_at,
    )
    session.add(sale)
    session.commit()
    return sale, vehicle


def department(report, key):
    return next(d for d in report.departments if d.key == key)


def adjustment(report, key):
    return next(a for a in report.adjustments if a.key == key)


@pytest.mark.asyncio
async def test_vehicles_split_by_condition_and_estimated_cost_count(env):
    admin, session, filial_id, _holding_id, _client = env
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    make_vehicle_sale(session, filial_id, VehicleCondition.NUEVO, 20000, True, 25000, now)
    make_vehicle_sale(session, filial_id, VehicleCondition.USADO, 8000, False, 11000, now)

    report = await admin.get_profitability(filial_id, date(2026, 6, 1), date(2026, 6, 30))

    nuevos = department(report, "vehiculos_nuevos")
    usados = department(report, "vehiculos_usados")
    assert nuevos.net_sales == 25000 and nuevos.direct_cost == 20000
    assert usados.net_sales == 11000 and usados.direct_cost == 8000
    assert report.vehicles_sold_count == 2
    assert report.vehicles_with_estimated_cost_count == 1


@pytest.mark.asyncio
async def test_repuestos_excludes_cancelled_sales(env):
    admin, session, filial_id, _holding_id, _client = env
    part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Filtro", price=10)
    session.add(part)
    session.commit()

    created_at = datetime(2026, 6, 15, tzinfo=timezone.utc)
    good_sale = PartSale(
        filial_id=filial_id, sequence_number=1, client_name="Cliente",
        status=PartSaleStatus.COMPLETADO, created_at=created_at,
    )
    cancelled_sale = PartSale(
        filial_id=filial_id, sequence_number=2, client_name="Cliente",
        status=PartSaleStatus.CANCELADO, created_at=created_at,
    )
    session.add_all([good_sale, cancelled_sale])
    session.commit()
    session.add(PartSaleLine(part_sale_id=good_sale.id, part_id=part.id, quantity=2, unit_price=15, unit_cost=10, line_total=30))
    session.add(PartSaleLine(part_sale_id=cancelled_sale.id, part_id=part.id, quantity=100, unit_price=15, unit_cost=10, line_total=1500))
    session.commit()

    report = await admin.get_profitability(filial_id, date(2026, 6, 1), date(2026, 6, 30))
    repuestos = department(report, "repuestos")
    assert repuestos.net_sales == 30
    assert repuestos.direct_cost == 20


@pytest.mark.asyncio
async def test_taller_revenue_recognized_at_issuance_not_collection(env):
    admin, session, filial_id, _holding_id, client = env
    vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Corolla", plate="ABC123")
    session.add(vehicle)
    session.commit()
    order = ServiceOrder(filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id)
    session.add(order)
    session.commit()
    invoice = ServiceOrderInvoice(
        service_order_id=order.id, request_id=uuid.uuid4(), request_hash="h", code="FAC-1",
        issued_at=datetime(2026, 6, 10, tzinfo=timezone.utc), total_usd=100, document={},
        billed_client_id=client.id, amount_paid_at_issuance=40, collected_at=None,
    )
    session.add(invoice)
    session.commit()

    report = await admin.get_profitability(filial_id, date(2026, 6, 1), date(2026, 6, 30))
    taller = department(report, "taller_mano_obra")
    assert taller.net_sales == 100  # full invoice total, not the 40 collected at issuance


@pytest.mark.asyncio
async def test_taller_direct_cost_uses_fifo_allocation_for_dispatched_lines(env):
    admin, session, filial_id, _holding_id, client = env
    warehouse = Warehouse(filial_id=filial_id, name="Principal")
    part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-2", name="Alternador", price=10)
    session.add_all([warehouse, part])
    session.commit()
    lot = PartLot(
        filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
        quantity_received=5, quantity_remaining=5, unit_cost=12, received_at=datetime.now(timezone.utc),
    )
    session.add(lot)
    session.commit()

    vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Corolla", plate="XYZ789")
    session.add(vehicle)
    session.commit()
    order = ServiceOrder(filial_id=filial_id, sequence_number=2, vehicle_id=vehicle.id)
    session.add(order)
    session.commit()
    transfer = ServiceOrderTransfer(service_order_id=order.id, sequence_number=1, status=TransferStatus.PEDIDO)
    session.add(transfer)
    session.commit()
    line = ServiceOrderTransferLine(transfer_id=transfer.id, part_id=part.id, quantity=3, cost_total=999)
    session.add(line)
    session.commit()
    session.add(ServiceOrderTransferLotAllocation(transfer_line_id=line.id, lot_id=lot.id, warehouse_id=warehouse.id, quantity=3, unit_cost=12))
    invoice = ServiceOrderInvoice(
        service_order_id=order.id, request_id=uuid.uuid4(), request_hash="h", code="FAC-2",
        issued_at=datetime(2026, 6, 12, tzinfo=timezone.utc), total_usd=200, document={},
        billed_client_id=client.id, amount_paid_at_issuance=200, collected_at=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )
    session.add(invoice)
    session.commit()

    report = await admin.get_profitability(filial_id, date(2026, 6, 1), date(2026, 6, 30))
    taller = department(report, "taller_mano_obra")
    assert taller.direct_cost == pytest.approx(36.0)  # 3 * 12, from the allocation — not the line's cost_total=999


@pytest.mark.asyncio
async def test_garantia_marca_and_fi_income_have_their_own_rows_and_count_as_manual(env):
    admin, _session, filial_id, _holding_id, _client = env
    account = await admin.create_account(
        AccountCreate(filial_id=filial_id, name="Caja", currency=AccountCurrency.USD, account_type=AccountType.CAJA)
    )
    today = date.today()

    await admin.create_income(
        IncomeEntryCreate(
            filial_id=filial_id, entry_date=today, concept=IncomeConcept.GARANTIA_MARCA,
            description="Reembolso Toyota", amount=50, currency=AccountCurrency.USD, account_id=account.id,
            counterparty_type=CounterpartyType.TERCERO, counterparty_name="Toyota de Venezuela",
        ),
        None, uuid.uuid4(),
    )
    await admin.create_income(
        IncomeEntryCreate(
            filial_id=filial_id, entry_date=today, concept=IncomeConcept.FI_INTERMEDIACION,
            description="Comisión de financiamiento", amount=20, currency=AccountCurrency.USD, account_id=account.id,
            counterparty_type=CounterpartyType.TERCERO, counterparty_name="Banco",
        ),
        None, uuid.uuid4(),
    )

    report = await admin.get_profitability(filial_id, today.replace(day=1), today)
    assert department(report, "garantia_marca").net_sales == 50
    assert department(report, "fi_intermediacion").net_sales == 20
    assert report.manual_movements_count >= 2
    assert report.manual_movements_rate > 0


@pytest.mark.asyncio
async def test_diferencia_en_cambio_only_counts_entries_with_a_frozen_rate(env):
    admin, session, filial_id, _holding_id, _client = env
    bs_account = await admin.create_account(
        AccountCreate(filial_id=filial_id, name="Caja Bs", currency=AccountCurrency.BS, account_type=AccountType.CAJA)
    )
    today = date.today()

    # Frozen at 40 (rate on file as of today), then the closing rate moves to 50.
    session.add(ExchangeRate(currency="USD", rate_ves=40, value_date=today))
    session.commit()
    entry = await admin.create_income(
        IncomeEntryCreate(
            filial_id=filial_id, entry_date=today, concept=IncomeConcept.OTRO_INGRESO,
            description="Ingreso en Bs", amount=3000, currency=AccountCurrency.BS, account_id=bs_account.id,
            counterparty_type=CounterpartyType.SOCIO, counterparty_name="Socio",
        ),
        None, uuid.uuid4(),
    )
    assert float(entry.amount_usd) == 75  # 3000 / 40

    # An automatic Bs entry with no frozen baseline — must be excluded.
    session.add(IncomeEntry(
        filial_id=filial_id, entry_date=today, source=IncomeSource.AUTOMATICO, description="Pago Bs de ODS",
        amount=9999, currency=AccountCurrency.BS, account_id=bs_account.id,
    ))
    session.commit()

    session.query(ExchangeRate).filter(ExchangeRate.currency == "USD").update({"rate_ves": 50})
    session.commit()

    report = await admin.get_profitability(filial_id, today.replace(day=1), today)
    fx = adjustment(report, "diferencia_cambio")
    # Reexpressed at 50: 3000/50 = 60; frozen was 75 -> diff = -15 (a loss).
    assert fx.amount == pytest.approx(-15.0)


@pytest.mark.asyncio
async def test_period_is_closed_reflects_current_calendar_month(env):
    admin, _session, filial_id, _holding_id, _client = env
    today = date.today()
    last_month_end = today.replace(day=1) - timedelta(days=1)

    open_report = await admin.get_profitability(filial_id, today.replace(day=1), today)
    closed_report = await admin.get_profitability(filial_id, last_month_end.replace(day=1), last_month_end)

    assert open_report.period_is_closed is False
    assert closed_report.period_is_closed is True


@pytest.mark.asyncio
async def test_bcv_rate_uses_the_rate_as_of_the_period_not_the_latest_one(env):
    admin, session, filial_id, _holding_id, _client = env
    session.add(ExchangeRate(currency="USD", rate_ves=30, value_date=date(2026, 1, 10)))
    session.add(ExchangeRate(currency="USD", rate_ves=99, value_date=date(2026, 3, 1)))
    session.commit()

    report = await admin.get_profitability(filial_id, date(2026, 1, 1), date(2026, 1, 31))
    assert report.bcv_rate == 30  # not 99, which is dated after this period


@pytest.mark.asyncio
async def test_holding_consolidation_sums_two_filiales(env):
    admin, session, filial_id, holding_id, _client = env
    other_filial_id = uuid.uuid4()
    session.add(Filial(id=other_filial_id, holding_id=holding_id, name="Otra Sucursal", slug="otra"))
    session.commit()

    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    make_vehicle_sale(session, filial_id, VehicleCondition.NUEVO, 10000, False, 12000, now)
    make_vehicle_sale(session, other_filial_id, VehicleCondition.NUEVO, 10000, False, 12000, now)

    report = await admin.get_profitability_for_holding(holding_id, date(2026, 6, 1), date(2026, 6, 30))
    assert report.filial_id is None
    assert report.vehicles_sold_count == 2
    assert department(report, "vehiculos_nuevos").net_sales == 24000
