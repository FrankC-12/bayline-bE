"""HTTP-level regression test for "guardar un cliente con su vehículo":
POST /clients with one vehicle, and with several, must return 201 with the
vehicles present in the response — and invalid data (a bad VIN, a missing
required field) must come back as a 4xx with the offending field named and
a Spanish message, never an unhandled 500. The service-level unit tests in
test_client_creation_plate.py already cover the specific plate/None crash
this was written for; this file exercises the same paths through the real
FastAPI app + exception handlers, the layer the reported 500 was seen at."""

import os
import uuid

os.environ["DEBUG"] = "false"

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
from app.modules.clients import router as clients_routes
from app.modules.filiales.models import Filial
from app.modules.roles.enums import RoleScope


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        session.commit()

        app = FastAPI()
        app.include_router(clients_routes.router, prefix="/api/v1")
        register_exception_handlers(app)

        db = AsyncAdapter(session)
        app.dependency_overrides[clients_routes.get_client_service] = lambda: clients_routes.ClientService(db)
        current_user = CurrentUser(
            user_id=uuid.uuid4(), email="admin@test.com", role_id=uuid.uuid4(), role_slug="filial-admin",
            scope=RoleScope.FILIAL, holding_id=None, filial_id=filial_id,
        )
        app.dependency_overrides[get_current_user] = lambda: current_user

        yield app, filial_id


def _base_payload(filial_id, **overrides):
    payload = {
        "filial_id": str(filial_id),
        "full_name": "José Ramírez",
        "client_type": "particular",
        "document_type": "V",
        "document_number": "12345678",
        "phone_primary": "04121234567",
        "address": "Caracas",
        "vehicles": [],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_create_client_with_one_vehicle_returns_201_and_the_vehicle_is_visible(env):
    app, filial_id = env
    payload = _base_payload(
        filial_id, vehicles=[{"brand": "Toyota", "model": "Hilux", "plate": "AB123CD"}]
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post("/api/v1/clients", json=payload)
        assert create_response.status_code == 201
        body = create_response.json()
        assert len(body["vehicles"]) == 1
        assert body["vehicles"][0]["plate"] == "AB123CD"

        get_response = await client.get(f"/api/v1/clients/{body['id']}")
        assert get_response.status_code == 200
        assert len(get_response.json()["vehicles"]) == 1


@pytest.mark.asyncio
async def test_create_client_with_several_vehicles_returns_201_and_all_are_visible(env):
    app, filial_id = env
    payload = _base_payload(
        filial_id,
        document_number="87654321",
        vehicles=[
            {"brand": "Toyota", "model": "Hilux", "plate": "AB123CD"},
            {"brand": "Ford", "model": "Fiesta", "plate": None},
            {"brand": "Chevrolet", "model": "Aveo", "plate": "XY987ZW"},
        ],
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post("/api/v1/clients", json=payload)
        assert create_response.status_code == 201
        body = create_response.json()
        assert len(body["vehicles"]) == 3
        assert {v["brand"] for v in body["vehicles"]} == {"Toyota", "Ford", "Chevrolet"}

        get_response = await client.get(f"/api/v1/clients/{body['id']}")
        assert len(get_response.json()["vehicles"]) == 3


@pytest.mark.asyncio
async def test_create_client_with_a_plateless_vehicle_does_not_500(env):
    """The exact scenario that used to crash: `plate: null` sent for a
    vehicle not yet registered ("sin placa")."""
    app, filial_id = env
    payload = _base_payload(
        filial_id, document_number="11223344", vehicles=[{"brand": "Toyota", "model": "Corolla", "plate": None}]
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/clients", json=payload)

    assert response.status_code == 201
    assert response.json()["vehicles"][0]["plate"] is None


@pytest.mark.asyncio
async def test_invalid_vehicle_vin_returns_422_with_the_field_named_in_spanish(env):
    app, filial_id = env
    payload = _base_payload(
        filial_id,
        document_number="55667788",
        vehicles=[{"brand": "Toyota", "model": "Hilux", "vin": "TOO-SHORT"}],
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/clients", json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["errorCode"] == "validation_error"
    field_errors = {d["field"]: d["message"] for d in body["details"]}
    assert field_errors["vehicles.0.vin"] == "El VIN debe tener exactamente 17 caracteres."


@pytest.mark.asyncio
async def test_missing_required_client_field_returns_422_with_the_field_named(env):
    app, filial_id = env
    payload = _base_payload(filial_id)
    del payload["phone_primary"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/clients", json=payload)

    assert response.status_code == 422
    body = response.json()
    field_errors = {d["field"]: d["message"] for d in body["details"]}
    assert field_errors["phone_primary"] == "Este campo es obligatorio."


@pytest.mark.asyncio
async def test_duplicate_document_number_returns_409_not_500(env):
    app, filial_id = env
    payload = _base_payload(filial_id, document_number="99998888")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/clients", json=payload)
        assert first.status_code == 201

        second = await client.post("/api/v1/clients", json=payload)

    assert second.status_code == 409
    assert second.json()["errorCode"] == "document_already_exists"
