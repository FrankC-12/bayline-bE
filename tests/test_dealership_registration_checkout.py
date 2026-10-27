"""Dealership registration, payment-channel and document access contracts."""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from test_inventory_vehicle_mileage import vehicle_input, sale_input
from test_vehicle_location import env as dealership_env
from app.core.exceptions import BadRequestError
from app.modules.concesionario.models import VehicleSale
from app.modules.concesionario.schemas import VehicleCreate, VehicleSaleInput, VehicleUpdate


@pytest.mark.parametrize(
    "method,usd,bs",
    [
        ("usd", "cash", None),
        ("usd", "binance", None),
        ("usd", "paypal", None),
        ("usd", "zelle", None),
        ("bs", None, "pago_movil"),
        ("bs", None, "transferencia"),
        ("mixed", "zelle", "pago_movil"),
    ],
)
def test_valid_payment_channels(method, usd, bs):
    sale = sale_input(payment_method=method, usd_payment_channel=usd, bs_payment_channel=bs)
    assert sale.usd_payment_channel == usd
    assert sale.bs_payment_channel == bs


@pytest.mark.parametrize(
    "overrides",
    [
        {"advisor_user_id": None},
        {"usd_payment_channel": None},
        {"usd_payment_channel": "unknown"},
        {"bs_payment_channel": "transferencia"},
        {"payment_method": "bs", "usd_payment_channel": None},
        {"payment_method": "mixed", "bs_payment_channel": None},
        {"sale_type": "financiado", "usd_payment_channel": "cash"},
    ],
)
def test_checkout_rejects_missing_or_incompatible_channels(overrides):
    with pytest.raises(ValidationError):
        sale_input(**overrides)


def test_checkout_requires_advisor_even_for_financed_sale():
    with pytest.raises(ValidationError, match="advisor_user_id"):
        VehicleSaleInput(client_name="Cliente", sale_type="financiado")


@pytest.mark.parametrize(
    "field,value", [("seats", 0), ("seats", 1.5), ("seats", True), ("previous_owners", -1)]
)
def test_registration_rejects_invalid_specifications(field, value):
    with pytest.raises(ValidationError):
        vehicle_input(uuid.uuid4(), **{field: value})
    with pytest.raises(ValidationError):
        VehicleUpdate(**{field: value})


def test_previous_owners_are_only_for_used_vehicles():
    with pytest.raises(ValidationError, match="dueños"):
        vehicle_input(uuid.uuid4(), previous_owners=1)
    payload = vehicle_input(uuid.uuid4(), condition="usado", mileage=50).model_dump()
    payload.pop("previous_owners")
    with pytest.raises(ValidationError, match="dueños"):
        VehicleCreate(**payload)


@pytest.mark.asyncio
async def test_sale_freezes_description_and_payment_channels(dealership_env):
    service, session, filial, caller = dealership_env
    vehicle = await service.create_vehicle(
        vehicle_input(filial, engine_type="2.0 turbo", seats=5, upholstery="Cuero")
    )
    await service.update_vehicle(
        vehicle.id, VehicleUpdate(status="vendido", sale=sale_input()), caller
    )
    sale = session.scalars(select(VehicleSale)).one()
    assert sale.usd_payment_channel == "cash"
    assert sale.vehicle_snapshot["engine_type"] == "2.0 turbo"
    await service.update_vehicle(
        vehicle.id, VehicleUpdate(engine_type="Otro motor", vin="1HGCM82633A004353"), caller
    )
    document = (await service.sale_document(sale))["html"]
    assert "2.0 turbo" in document and "Otro motor" not in document
    assert "1HGCM82633A004352" in document and "1HGCM82633A004353" not in document
    assert "Cuero" in document


@pytest.mark.asyncio
async def test_used_vehicle_cannot_be_sold_without_documents(dealership_env):
    service, session, filial, caller = dealership_env
    vehicle = await service.create_vehicle(vehicle_input(filial, condition="usado", mileage=100))
    with pytest.raises(BadRequestError, match="certificado"):
        await service.update_vehicle(
            vehicle.id, VehicleUpdate(status="vendido", sale=sale_input()), caller
        )
    assert session.scalars(select(VehicleSale)).all() == []


@pytest.mark.asyncio
async def test_private_document_upload_download_and_filial_scope(
    dealership_env, tmp_path, monkeypatch
):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.core.config import get_settings
    from app.core.exception_handlers import register_exception_handlers
    from app.modules.auth.dependencies import get_current_user
    from app.modules.concesionario import router as routes

    service, session, filial, caller = dealership_env
    vehicle = await service.create_vehicle(vehicle_input(filial, condition="usado", mileage=100))
    settings = get_settings()
    monkeypatch.setattr(settings, "private_uploads_dir", str(tmp_path))
    monkeypatch.setattr(settings, "s3_private_bucket_name", None)
    user = caller.model_copy(update={"role_slug": "filial-admin"})
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: user
    path = f"/dealership-vehicles/{vehicle.id}/documents/titulo"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        rejected = await http.post(path, files={"file": ("bad.html", b"<script>", "text/html")})
        assert rejected.status_code == 400
        result = await http.post(
            path, files={"file": ("titulo.pdf", b"%PDF-1.4\nprivate evidence", "application/pdf")}
        )
        assert result.status_code == 200, result.text
        assert "private://" not in result.text and str(tmp_path) not in result.text
        assert result.json()["registration_documents"]["titulo"].endswith(path)
        assert (await http.get(path)).content == b"%PDF-1.4\nprivate evidence"
        app.dependency_overrides[get_current_user] = lambda: user.model_copy(
            update={"filial_id": uuid.uuid4()}
        )
        assert (await http.get(path)).status_code == 403
        assert (
            await http.post(path, files={"file": ("titulo.pdf", b"%PDF-1.4", "application/pdf")})
        ).status_code == 403


@pytest.mark.asyncio
async def test_invoice_flow_fetches_only_selected_vehicle_and_checks_access(dealership_env):
    from app.modules.concesionario.router import vehicle_sale_for_vehicle
    from app.core.exceptions import ForbiddenError, NotFoundError

    service, session, filial, caller = dealership_env
    vehicle = await service.create_vehicle(vehicle_input(filial))
    admin = caller.model_copy(update={"role_slug": "filial-admin"})
    with pytest.raises(NotFoundError):
        await vehicle_sale_for_vehicle(vehicle.id, admin, service)
    await service.update_vehicle(
        vehicle.id, VehicleUpdate(status="vendido", sale=sale_input()), caller
    )
    sale = await vehicle_sale_for_vehicle(vehicle.id, admin, service)
    assert sale.vehicle_id == vehicle.id
    with pytest.raises(ForbiddenError):
        await vehicle_sale_for_vehicle(
            vehicle.id, admin.model_copy(update={"filial_id": uuid.uuid4()}), service
        )


@pytest.mark.asyncio
async def test_used_owner_fields_follow_condition_on_edit(dealership_env):
    service, session, filial, caller = dealership_env
    vehicle = await service.create_vehicle(vehicle_input(filial, mileage=0))
    with pytest.raises(BadRequestError, match="solo corresponden"):
        await service.update_vehicle(vehicle.id, VehicleUpdate(previous_owners=1), caller)
    with pytest.raises(BadRequestError, match="dueños"):
        await service.update_vehicle(vehicle.id, VehicleUpdate(condition="usado"), caller)
    await service.update_vehicle(
        vehicle.id, VehicleUpdate(condition="usado", previous_owners=1), caller
    )
    assert vehicle.previous_owners == 1
    await service.update_vehicle(vehicle.id, VehicleUpdate(condition="nuevo"), caller)
    assert vehicle.previous_owners is None
