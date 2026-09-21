"""The Holding summary dashboard rolls up ODS, ODT, ventas de repuestos,
ventas de vehículos, clientes, usuarios y almacenes across every filial in
the holding — reusing each module's existing per-filial listing, not new
business logic. One filial gets real data, a second stays empty, to prove
both the counts and the per-filial isolation are correct."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client
from app.modules.concesionario.enums import SaleType, VehicleCondition, VehicleStatus
from app.modules.concesionario.models import DealershipVehicle, VehicleSale
from app.modules.filiales.models import Filial
from app.modules.holdings.dashboard import HoldingDashboardService
from app.modules.holdings.models import Holding
from app.modules.parts.enums import PartSaleStatus
from app.modules.parts.models import Part, PartSale, PartSaleLine
from app.modules.roles.models import Role
from app.modules.roles.enums import RoleScope
from app.modules.service_orders.enums import ServiceOrderStatus, TransferStatus
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.service import ServiceOrderService
from app.modules.users.enums import UserStatus
from app.modules.users.models import User
from app.modules.warehouse.models import Warehouse


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        holding = Holding(name="Grupo Toyoval", slug="grupo-toyoval")
        session.add(holding)
        session.commit()
        filial_a = Filial(holding_id=holding.id, name="Taller Este", slug="taller-este")
        filial_b = Filial(holding_id=holding.id, name="Taller Vacío", slug="taller-vacio")
        session.add_all([filial_a, filial_b])
        session.commit()
        db = AsyncAdapter(session)
        yield HoldingDashboardService(db), ServiceOrderService(db), session, holding, filial_a, filial_b


async def _seed_filial_a(session, service, filial):
    role = Role(name="Asesor", slug="asesor", scope=RoleScope.FILIAL)
    session.add(role)
    session.commit()

    client = Client(
        filial_id=filial.id, full_name="Cliente Uno", client_type=ClientType.PARTICULAR,
        document_type=DocumentType.V, document_number="12345678", phone_primary="04121234567", address="Caracas",
    )
    session.add(client)
    session.add(
        Client(
            filial_id=filial.id, full_name="Cliente Dos", client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V, document_number="87654321", phone_primary="04121234567", address="Caracas",
        )
    )
    session.commit()

    order = ServiceOrder(filial_id=filial.id, sequence_number=1, vehicle_id=uuid.uuid4(), status=ServiceOrderStatus.EN_PROGRESO)
    session.add(order)
    session.commit()
    part = Part(category_id=uuid.uuid4(), filial_id=filial.id, code="P-1", name="Filtro", price=10, stock_quantity=5)
    session.add(part)
    session.commit()
    await service.add_transfer_line(order.id, part.id, 1)

    part_sale = PartSale(
        filial_id=filial.id, sequence_number=5001, client_name="Cliente Uno", status=PartSaleStatus.COMPLETADO,
        created_at=datetime.now(timezone.utc),
    )
    session.add(part_sale)
    session.commit()
    session.add(
        PartSaleLine(part_sale_id=part_sale.id, part_id=part.id, quantity=1, unit_price=50, unit_cost=35, line_total=50)
    )
    session.commit()

    vehicle = DealershipVehicle(
        filial_id=filial.id, status=VehicleStatus.VENDIDO, condition=VehicleCondition.NUEVO,
        brand="Toyota", model="Corolla", year=2024, vin="1HGCM82633A123456", sku="COR-2024",
        price_cash=20000, price_financed=22000,
    )
    session.add(vehicle)
    session.commit()
    session.add(
        VehicleSale(
            filial_id=filial.id, sequence_number=9001, vehicle_id=vehicle.id, client_name="Cliente Uno",
            sale_type=SaleType.CONTADO, final_price=21000,
        )
    )
    session.add_all(
        [
            User(full_name="Activo", email="activo@taller.com", status=UserStatus.ACTIVO, role_id=role.id, filial_id=filial.id),
            User(full_name="Inactivo", email="inactivo@taller.com", status=UserStatus.INACTIVO, role_id=role.id, filial_id=filial.id),
        ]
    )
    session.add_all(
        [
            Warehouse(filial_id=filial.id, name="Principal", is_active=True),
            Warehouse(filial_id=filial.id, name="Descontinuado", is_active=False),
        ]
    )
    session.commit()
    return order


@pytest.mark.asyncio
async def test_dashboard_rolls_up_every_metric_per_filial(env):
    dashboard, orders_service, session, holding, filial_a, filial_b = env
    await _seed_filial_a(session, orders_service, filial_a)

    report = await dashboard.get_dashboard(holding.id)

    rows = {row.filial_id: row for row in report.filiales}
    assert set(rows) == {filial_a.id, filial_b.id}

    row_a = rows[filial_a.id]
    assert row_a.ods.total == 1
    assert row_a.ods.en_progreso == 1
    assert row_a.odt.total == 1
    assert row_a.odt.pendiente == 1
    assert row_a.odt.pedido == 0
    assert row_a.ventas_repuestos == _sales(count=1, total_usd=50.0)
    assert row_a.ventas_vehiculos == _sales(count=1, total_usd=21000.0)
    assert row_a.clientes == 2
    assert row_a.usuarios_total == 2
    assert row_a.usuarios_activos == 1
    assert row_a.almacenes_total == 2
    assert row_a.almacenes_activos == 1

    row_b = rows[filial_b.id]
    assert row_b.ods.total == 0
    assert row_b.odt.total == 0
    assert row_b.ventas_repuestos.count == 0
    assert row_b.ventas_vehiculos.count == 0
    assert row_b.clientes == 0
    assert row_b.usuarios_total == 0
    assert row_b.almacenes_total == 0


@pytest.mark.asyncio
async def test_cancelled_part_sale_is_excluded_from_the_revenue_total(env):
    dashboard, orders_service, session, holding, filial_a, filial_b = env
    part = Part(category_id=uuid.uuid4(), filial_id=filial_a.id, code="P-2", name="Aceite", price=10, stock_quantity=5)
    session.add(part)
    session.commit()
    cancelled = PartSale(
        filial_id=filial_a.id, sequence_number=5002, client_name="Cliente Uno", status=PartSaleStatus.CANCELADO,
        created_at=datetime.now(timezone.utc),
    )
    session.add(cancelled)
    session.commit()
    session.add(
        PartSaleLine(part_sale_id=cancelled.id, part_id=part.id, quantity=1, unit_price=999, unit_cost=700, line_total=999)
    )
    session.commit()

    report = await dashboard.get_dashboard(holding.id)
    row_a = next(r for r in report.filiales if r.filial_id == filial_a.id)

    assert row_a.ventas_repuestos.count == 1
    assert row_a.ventas_repuestos.total_usd == 0.0


def _sales(count, total_usd):
    from app.modules.holdings.schemas import SalesSummary

    return SalesSummary(count=count, total_usd=total_usd)
