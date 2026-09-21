"""Selling a dealership vehicle below its cost_price now requires an
"administracion"-level authorization (the vendedor who makes the sale never
has that access on its own), and leaves who authorized it and when."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.concesionario import router as routes
from app.modules.concesionario.enums import SaleType, VehicleCondition, VehicleStatus
from app.modules.concesionario.exceptions import (
    BelowCostOverrideNoteRequiredError,
    BelowCostSaleRequiresAuthorizationError,
)
from app.modules.concesionario.models import DealershipVehicle, VehicleSale
from app.modules.concesionario.schemas import VehicleSaleInput, VehicleUpdate
from app.modules.concesionario.service import ConcesionarioService
from app.modules.filiales.models import Filial
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission


def _user(*, role_id, role_slug, filial_id) -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(), email="user@test.com", role_id=role_id, role_slug=role_slug,
        scope=RoleScope.FILIAL, holding_id=None, filial_id=filial_id,
    )


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Concesionario", slug="conce"))
        vendedor_role = Role(name="Vendedor", slug="vendedor", scope=RoleScope.FILIAL)
        admin_role = Role(name="Administrador", slug="administrador", scope=RoleScope.FILIAL)
        session.add_all([vendedor_role, admin_role])
        session.commit()
        session.add_all(
            [
                RoleModulePermission(role_id=vendedor_role.id, module_id="concesionario", access=AccessLevel.EDITAR),
                RoleModulePermission(role_id=admin_role.id, module_id="concesionario", access=AccessLevel.EDITAR),
                RoleModulePermission(role_id=admin_role.id, module_id="administracion", access=AccessLevel.EDITAR),
            ]
        )
        vehicle = DealershipVehicle(
            filial_id=filial_id, status=VehicleStatus.DISPONIBLE, condition=VehicleCondition.NUEVO,
            brand="Toyota", model="Corolla", year=2026, vin=str(uuid.uuid4())[:17], sku="SKU-1",
            price_cash=8_000, price_financed=8_500, cost_price=10_000,
        )
        session.add(vehicle)
        session.commit()
        db = AsyncAdapter(session)
        vendedor = _user(role_id=vendedor_role.id, role_slug="vendedor", filial_id=filial_id)
        admin = _user(role_id=admin_role.id, role_slug="administrador", filial_id=filial_id)
        yield ConcesionarioService(db), session, filial_id, vehicle, vendedor, admin


def _sale(**overrides):
    payload = dict(client_name="Cliente de Prueba", sale_type=SaleType.CONTADO, payment_method="usd")
    payload.update(overrides)
    return VehicleSaleInput(**payload)


@pytest.mark.asyncio
async def test_below_cost_sale_without_override_is_rejected(env):
    service, _session, _filial_id, vehicle, vendedor, _admin = env
    with pytest.raises(BelowCostSaleRequiresAuthorizationError):
        await service.update_vehicle(
            vehicle.id, VehicleUpdate(status=VehicleStatus.VENDIDO, sale=_sale()), vendedor
        )


@pytest.mark.asyncio
async def test_below_cost_sale_with_override_but_no_note_is_rejected(env):
    service, _session, _filial_id, vehicle, _vendedor, admin = env
    with pytest.raises(BelowCostOverrideNoteRequiredError):
        await service.update_vehicle(
            vehicle.id,
            VehicleUpdate(status=VehicleStatus.VENDIDO, sale=_sale(below_cost_override=True)),
            admin,
        )


@pytest.mark.asyncio
async def test_a_vendedor_cannot_authorize_their_own_below_cost_sale(env):
    from app.modules.auth.exceptions import InsufficientPermissionsError

    service, _session, _filial_id, vehicle, vendedor, _admin = env
    with pytest.raises(InsufficientPermissionsError):
        await service.update_vehicle(
            vehicle.id,
            VehicleUpdate(
                status=VehicleStatus.VENDIDO,
                sale=_sale(below_cost_override=True, below_cost_override_note="Cliente frecuente, cierre urgente."),
            ),
            vendedor,
        )


@pytest.mark.asyncio
async def test_an_administrador_can_authorize_a_below_cost_sale_and_it_is_recorded(env):
    service, session, _filial_id, vehicle, _vendedor, admin = env
    updated = await service.update_vehicle(
        vehicle.id,
        VehicleUpdate(
            status=VehicleStatus.VENDIDO,
            sale=_sale(below_cost_override=True, below_cost_override_note="Autorizado para liquidar inventario."),
        ),
        admin,
    )
    assert updated.status == VehicleStatus.VENDIDO

    sale = session.query(VehicleSale).filter_by(vehicle_id=vehicle.id).one()
    assert sale.below_cost_override is True
    assert sale.below_cost_override_note == "Autorizado para liquidar inventario."
    assert sale.authorized_by_user_id == admin.user_id
    assert sale.authorized_at is not None


@pytest.mark.asyncio
async def test_an_at_or_above_cost_sale_needs_no_authorization_and_records_none(env):
    service, session, _filial_id, vehicle, vendedor, _admin = env
    vehicle.cost_price = 5_000  # below both price_cash (8000) and price_financed (8500)
    session.commit()

    updated = await service.update_vehicle(
        vehicle.id, VehicleUpdate(status=VehicleStatus.VENDIDO, sale=_sale()), vendedor
    )
    assert updated.status == VehicleStatus.VENDIDO

    sale = session.query(VehicleSale).filter_by(vehicle_id=vehicle.id).one()
    assert sale.below_cost_override is False
    assert sale.below_cost_override_note is None
    assert sale.authorized_by_user_id is None
    assert sale.authorized_at is None


@pytest.mark.asyncio
async def test_http_below_cost_sale_is_rejected_with_a_clear_message(env):
    _service, session, filial_id, vehicle, vendedor, _admin = env
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    register_exception_handlers(app)
    db = AsyncAdapter(session)
    app.dependency_overrides[routes.get_service] = lambda: ConcesionarioService(db)
    app.dependency_overrides[get_current_user] = lambda: vendedor

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            f"/api/v1/dealership-vehicles/{vehicle.id}",
            json={
                "status": "vendido",
                "sale": {"client_name": "Cliente", "sale_type": "contado", "payment_method": "usd"},
            },
        )
    assert response.status_code == 400
    assert "autorización" in response.json()["message"]
