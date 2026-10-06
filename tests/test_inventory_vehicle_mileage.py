"""Inventory mileage is mandatory for used units and frozen into the sale."""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_vehicle_location import env as mileage_env_fixture

from app.core.exceptions import BadRequestError
from app.modules.concesionario.models import VehicleSale
from app.modules.concesionario.schemas import VehicleCreate, VehicleSaleInput, VehicleUpdate

inventory_env = mileage_env_fixture


def vehicle_input(filial_id, **overrides):
    values = dict(
        filial_id=filial_id,
        status="disponible",
        condition="nuevo",
        location="patio",
        brand="Mazda",
        model="3",
        year=2022,
        vin=str(uuid.uuid4())[:17],
        sku="MAZDA3",
        price_cash=10000,
        price_financed=12000,
        cost_price=8000,
    )
    values.update(overrides)
    return VehicleCreate(**values)


def sale_input(**overrides):
    values = dict(client_name="Cliente", sale_type="contado", payment_method="usd")
    values.update(overrides)
    return VehicleSaleInput(**values)


def test_used_vehicle_requires_mileage_and_zero_is_valid():
    with pytest.raises(ValidationError, match="kilometraje"):
        vehicle_input(uuid.uuid4(), condition="usado")
    assert vehicle_input(uuid.uuid4(), condition="usado", mileage=0).mileage == 0
    assert vehicle_input(uuid.uuid4(), condition="nuevo").mileage is None


@pytest.mark.parametrize("value", [-1, 1.5, True, "5000", 2147483648])
def test_mileage_rejects_invalid_values_for_creation_and_updates(value):
    with pytest.raises(ValidationError):
        vehicle_input(uuid.uuid4(), mileage=value)
    with pytest.raises(ValidationError):
        VehicleUpdate(mileage=value)


@pytest.mark.asyncio
async def test_used_mileage_is_persisted_and_can_be_edited(inventory_env):
    service, session, filial_id, user = inventory_env
    vehicle = await service.create_vehicle(
        vehicle_input(filial_id, condition="usado", mileage=65000)
    )
    assert vehicle.mileage == 65000
    updated = await service.update_vehicle(
        vehicle.id, VehicleUpdate(mileage=66000, year=1980), user
    )
    session.expire_all()
    assert (await service.get_vehicle(vehicle.id)).mileage == 66000
    assert updated.year == 1980
    # An unrelated edit retains the known odometer rather than replacing it.
    await service.update_vehicle(vehicle.id, VehicleUpdate(color="Azul"), user)
    assert vehicle.mileage == 66000
    with pytest.raises(BadRequestError, match="kilometraje"):
        await service.update_vehicle(vehicle.id, VehicleUpdate(mileage=None), user)
    assert vehicle.mileage == 66000


@pytest.mark.asyncio
async def test_changing_to_used_requires_existing_or_supplied_mileage(inventory_env):
    service, session, filial_id, user = inventory_env
    vehicle = await service.create_vehicle(vehicle_input(filial_id))
    with pytest.raises(BadRequestError, match="kilometraje"):
        await service.update_vehicle(vehicle.id, VehicleUpdate(condition="usado"), user)
    assert vehicle.condition.value == "nuevo"
    await service.update_vehicle(vehicle.id, VehicleUpdate(condition="usado", mileage=50000), user)
    assert vehicle.condition.value == "usado"
    assert vehicle.mileage == 50000


@pytest.mark.asyncio
async def test_new_unit_can_clear_optional_mileage(inventory_env):
    service, session, filial_id, user = inventory_env
    vehicle = await service.create_vehicle(vehicle_input(filial_id, mileage=0))
    await service.update_vehicle(vehicle.id, VehicleUpdate(mileage=None), user)
    assert vehicle.mileage is None


@pytest.mark.asyncio
async def test_sale_snapshot_and_document_do_not_change_with_current_odometer(inventory_env):
    service, session, filial_id, user = inventory_env
    vehicle = await service.create_vehicle(
        vehicle_input(filial_id, condition="usado", mileage=65000)
    )
    await service.update_vehicle(
        vehicle.id,
        VehicleUpdate(status="vendido", sale=sale_input(client_name="<script>alert('x')</script>")),
        user,
    )
    sale = session.scalars(select(VehicleSale)).one()
    assert sale.mileage_at_sale == 65000
    await service.update_vehicle(vehicle.id, VehicleUpdate(mileage=90000), user)
    document = await service.sale_document(sale)
    assert document["filename"] == f"{sale.code}.html"
    assert "65.000 km" in document["html"]
    assert "90.000 km" not in document["html"]
    assert "<script>" not in document["html"]
    assert "&lt;script&gt;" in document["html"]


@pytest.mark.asyncio
async def test_mileage_supplied_with_sale_is_frozen_before_sale_persistence(inventory_env):
    service, session, filial_id, user = inventory_env
    vehicle = await service.create_vehicle(vehicle_input(filial_id, condition="usado", mileage=100))
    await service.update_vehicle(
        vehicle.id, VehicleUpdate(status="vendido", mileage=200, sale=sale_input()), user
    )
    assert session.scalars(select(VehicleSale)).one().mileage_at_sale == 200
    assert vehicle.mileage == 200


@pytest.mark.asyncio
async def test_http_used_creation_and_edit_and_document_scope(inventory_env):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core.exception_handlers import register_exception_handlers
    from app.modules.auth.dependencies import get_current_user
    from app.modules.concesionario import router as routes

    service, session, filial_id, user = inventory_env
    caller = user.model_copy(update={"role_slug": "filial-admin"})
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: caller
    payload = vehicle_input(filial_id, condition="usado", mileage=65000).model_dump(mode="json")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        missing = await http.post(
            "/dealership-vehicles", json={k: v for k, v in payload.items() if k != "mileage"}
        )
        assert missing.status_code == 422
        created = await http.post("/dealership-vehicles", json=payload)
        assert created.status_code == 201
        assert created.json()["mileage"] == 65000
        vehicle_id = created.json()["id"]
        clearing = await http.patch(f"/dealership-vehicles/{vehicle_id}", json={"mileage": None})
        assert clearing.status_code == 400
        sold = await http.patch(
            f"/dealership-vehicles/{vehicle_id}",
            json={"status": "vendido", "sale": sale_input().model_dump(mode="json")},
        )
        assert sold.status_code == 200
        sales = await http.get(f"/vehicle-sales?filial_id={filial_id}")
        assert sales.status_code == 200
        assert sales.json()[0]["mileage_at_sale"] == 65000
        document_path = f"/vehicle-sales/{sales.json()[0]['id']}/document"
        document = await http.get(document_path)
        assert document.status_code == 200
        assert "65.000 km" in document.json()["html"]
        app.dependency_overrides[get_current_user] = lambda: caller.model_copy(
            update={"filial_id": uuid.uuid4()}
        )
        assert (await http.get(document_path)).status_code == 403
