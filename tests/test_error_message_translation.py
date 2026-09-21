"""Server error responses must be in Spanish and, when a single field caused
the failure, name that field so the frontend can anchor the message to it.
Pydantic v2's built-in validation messages ("Field required", "String
should have at least...") are the one place English still leaked — every
custom DomainError/validator in this codebase already raises Spanish."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field, field_validator

from app.core.exception_handlers import register_exception_handlers


class _Payload(BaseModel):
    name: str = Field(min_length=3, max_length=10)
    quantity: int = Field(ge=1)
    vehicle_id: uuid.UUID

    @field_validator("name")
    @classmethod
    def _no_spaces(cls, v: str) -> str:
        if " " in v:
            raise ValueError("El nombre no puede tener espacios.")
        return v


@pytest.fixture
def app():
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/echo")
    async def echo(payload: _Payload):
        return payload.model_dump(mode="json")

    @app.get("/boom")
    async def boom():
        raise RuntimeError("something broke")

    return app


@pytest.mark.asyncio
async def test_single_missing_field_names_the_field(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/echo", json={"quantity": 1, "vehicle_id": str(uuid.uuid4())})

    assert response.status_code == 422
    body = response.json()
    assert body["message"] == "name: Este campo es obligatorio."
    assert body["details"] == [{"field": "name", "message": "Este campo es obligatorio."}]


@pytest.mark.asyncio
async def test_string_too_short_is_translated_with_the_limit(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/echo", json={"name": "ab", "quantity": 1, "vehicle_id": str(uuid.uuid4())})

    body = response.json()
    assert body["message"] == "name: Debe tener al menos 3 caracteres."


@pytest.mark.asyncio
async def test_quantity_below_minimum_is_translated(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/echo", json={"name": "abc", "quantity": 0, "vehicle_id": str(uuid.uuid4())})

    body = response.json()
    assert body["message"] == "quantity: Debe ser mayor o igual a 1."


@pytest.mark.asyncio
async def test_invalid_uuid_is_translated(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/echo", json={"name": "abc", "quantity": 1, "vehicle_id": "not-a-uuid"})

    body = response.json()
    assert body["message"] == "vehicle_id: Debe ser un identificador válido."


@pytest.mark.asyncio
async def test_custom_validator_message_passes_through_in_spanish_without_english_prefix(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/echo", json={"name": "a b", "quantity": 1, "vehicle_id": str(uuid.uuid4())}
        )

    body = response.json()
    assert body["message"] == "name: El nombre no puede tener espacios."
    assert "Value error" not in body["message"]


@pytest.mark.asyncio
async def test_multiple_field_errors_give_a_generic_spanish_summary_with_per_field_details(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/echo", json={"quantity": 0})

    body = response.json()
    assert body["message"] == "Hay campos inválidos o incompletos — revisa los detalles marcados."
    fields = {d["field"] for d in body["details"]}
    assert fields == {"name", "quantity", "vehicle_id"}


@pytest.mark.asyncio
async def test_unexpected_error_message_is_in_spanish(app):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    assert response.json()["message"] == "Ocurrió un error inesperado. Intenta de nuevo."
