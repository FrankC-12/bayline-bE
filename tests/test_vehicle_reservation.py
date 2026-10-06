"""Vehicle status transitions are now a defined graph (not a blind field
assignment), and reserving requires a client, salesperson, deposit and
validity date, locking the unit against every other salesperson until it's
released or sold."""

import uuid
from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exceptions import BadRequestError
from app.modules.auth.schemas import CurrentUser
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client
from app.modules.concesionario.enums import VehicleCondition, VehicleStatus
from app.modules.concesionario.exceptions import (
    InvalidVehicleStatusTransitionError,
    VehicleReservedByAnotherUserError,
)
from app.modules.concesionario.models import DealershipVehicle
from app.modules.concesionario.schemas import VehicleReservationInput, VehicleUpdate
from app.modules.concesionario.service import ConcesionarioService
from app.modules.filiales.models import Filial
from app.modules.roles.enums import RoleScope


def _user(*, role_slug="vendedor", user_id=None, filial_id) -> CurrentUser:
    return CurrentUser(
        user_id=user_id or uuid.uuid4(),
        email="user@test.com",
        role_id=uuid.uuid4(),
        role_slug=role_slug,
        scope=RoleScope.FILIAL,
        holding_id=None,
        filial_id=filial_id,
    )


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(
            Filial(id=filial_id, holding_id=uuid.uuid4(), name="Concesionario", slug="conce")
        )
        client = Client(
            filial_id=filial_id,
            full_name="Cliente",
            client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V,
            document_number="12345678",
            phone_primary="04121234567",
            address="Caracas",
        )
        session.add(client)
        session.commit()
        vehicle = DealershipVehicle(
            filial_id=filial_id,
            status=VehicleStatus.DISPONIBLE,
            condition=VehicleCondition.NUEVO,
            brand="Toyota",
            model="Corolla",
            year=2026,
            vin=str(uuid.uuid4())[:17],
            sku="SKU-1",
            price_cash=10_000,
            price_financed=11_000,
            cost_price=8_000,
        )
        session.add(vehicle)
        session.commit()
        db = AsyncAdapter(session)
        yield ConcesionarioService(db), session, filial_id, vehicle, client


@pytest.mark.asyncio
async def test_reserving_requires_client_deposit_and_validity(env):
    service, _session, _filial_id, vehicle, _client = env
    with pytest.raises(ValidationError):
        VehicleReservationInput(
            advisor_user_id=uuid.uuid4(), deposit_amount=0, expires_at=date.today()
        )


@pytest.mark.asyncio
async def test_reserve_vehicle_locks_it_to_the_chosen_advisor(env):
    service, session, filial_id, vehicle, client = env
    advisor_id = uuid.uuid4()
    caller = _user(filial_id=filial_id, user_id=advisor_id)

    reserved = await service.reserve_vehicle(
        vehicle.id,
        VehicleReservationInput(
            client_id=client.id,
            advisor_user_id=advisor_id,
            deposit_amount=500,
            expires_at=date.today() + timedelta(days=7),
        ),
        caller,
    )

    assert reserved.status == VehicleStatus.RESERVADO
    assert reserved.reserved_client_id == client.id
    assert reserved.reserved_by_user_id == advisor_id
    assert float(reserved.deposit_amount) == 500
    assert reserved.reservation_expires_at == date.today() + timedelta(days=7)
    assert reserved.reserved_at is not None


@pytest.mark.asyncio
async def test_reserving_with_a_past_expiration_is_rejected(env):
    service, _session, filial_id, vehicle, client = env
    caller = _user(filial_id=filial_id)
    with pytest.raises(BadRequestError):
        await service.reserve_vehicle(
            vehicle.id,
            VehicleReservationInput(
                client_id=client.id,
                advisor_user_id=caller.user_id,
                deposit_amount=500,
                expires_at=date.today() - timedelta(days=1),
            ),
            caller,
        )


@pytest.mark.asyncio
async def test_another_salesperson_cannot_touch_a_reserved_vehicle(env):
    service, _session, filial_id, vehicle, client = env
    owner = _user(filial_id=filial_id)
    stranger = _user(filial_id=filial_id)

    await service.reserve_vehicle(
        vehicle.id,
        VehicleReservationInput(
            client_id=client.id,
            advisor_user_id=owner.user_id,
            deposit_amount=500,
            expires_at=date.today() + timedelta(days=7),
        ),
        owner,
    )

    with pytest.raises(VehicleReservedByAnotherUserError):
        await service.update_vehicle(vehicle.id, VehicleUpdate(color="Rojo"), stranger)

    with pytest.raises(VehicleReservedByAnotherUserError):
        await service.reserve_vehicle(
            vehicle.id,
            VehicleReservationInput(
                client_id=client.id,
                advisor_user_id=stranger.user_id,
                deposit_amount=100,
                expires_at=date.today() + timedelta(days=1),
            ),
            stranger,
        )


@pytest.mark.asyncio
async def test_a_filial_admin_can_override_someone_elses_reservation(env):
    service, _session, filial_id, vehicle, client = env
    owner = _user(filial_id=filial_id)
    admin = _user(filial_id=filial_id, role_slug="filial-admin")

    await service.reserve_vehicle(
        vehicle.id,
        VehicleReservationInput(
            client_id=client.id,
            advisor_user_id=owner.user_id,
            deposit_amount=500,
            expires_at=date.today() + timedelta(days=7),
        ),
        owner,
    )

    released = await service.update_vehicle(
        vehicle.id,
        VehicleUpdate(status=VehicleStatus.DISPONIBLE, status_reason="Cliente cancela la reserva"),
        admin,
    )
    assert released.status == VehicleStatus.DISPONIBLE
    assert released.reserved_by_user_id is None


@pytest.mark.asyncio
async def test_releasing_a_reservation_clears_its_fields(env):
    service, _session, filial_id, vehicle, client = env
    owner = _user(filial_id=filial_id)

    await service.reserve_vehicle(
        vehicle.id,
        VehicleReservationInput(
            client_id=client.id,
            advisor_user_id=owner.user_id,
            deposit_amount=500,
            expires_at=date.today() + timedelta(days=7),
        ),
        owner,
    )

    released = await service.update_vehicle(
        vehicle.id,
        VehicleUpdate(status=VehicleStatus.DISPONIBLE, status_reason="Cliente cancela la reserva"),
        owner,
    )
    assert released.status == VehicleStatus.DISPONIBLE
    assert released.reserved_client_id is None
    assert released.reserved_by_user_id is None
    assert released.deposit_amount is None
    assert released.reservation_expires_at is None
    assert released.reserved_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current,target",
    [
        (VehicleStatus.EN_TRANSITO, VehicleStatus.VENDIDO),
        (VehicleStatus.EN_TRANSITO, VehicleStatus.RESERVADO),
        (VehicleStatus.EN_PREPARACION, VehicleStatus.RESERVADO),
        (VehicleStatus.EN_PREPARACION, VehicleStatus.VENDIDO),
    ],
)
async def test_disallowed_transitions_are_rejected_with_a_message(env, current, target):
    service, session, filial_id, vehicle, client = env
    caller = _user(filial_id=filial_id)
    vehicle.status = current
    session.commit()

    if target == VehicleStatus.RESERVADO:
        with pytest.raises(InvalidVehicleStatusTransitionError) as excinfo:
            await service.reserve_vehicle(
                vehicle.id,
                VehicleReservationInput(
                    client_id=client.id,
                    advisor_user_id=caller.user_id,
                    deposit_amount=500,
                    expires_at=date.today() + timedelta(days=7),
                ),
                caller,
            )
    else:
        with pytest.raises(InvalidVehicleStatusTransitionError) as excinfo:
            await service.update_vehicle(vehicle.id, VehicleUpdate(status=target), caller)

    assert current.value in str(excinfo.value)
    assert target.value in str(excinfo.value)


@pytest.mark.asyncio
async def test_patching_status_to_reservado_directly_is_blocked(env):
    """Reserving must go through the dedicated action — it needs data a
    plain PATCH doesn't collect (client, advisor, deposit, expiration)."""
    service, _session, filial_id, vehicle, _client = env
    caller = _user(filial_id=filial_id)
    with pytest.raises(BadRequestError):
        await service.update_vehicle(
            vehicle.id, VehicleUpdate(status=VehicleStatus.RESERVADO), caller
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [VehicleStatus.EN_TRANSITO, VehicleStatus.EN_PREPARACION])
async def test_reserved_vehicle_cannot_jump_back_to_shipping_or_preparation(env, target):
    service, session, filial_id, vehicle, client = env
    owner = _user(filial_id=filial_id)
    await service.reserve_vehicle(
        vehicle.id,
        VehicleReservationInput(
            client_id=client.id,
            advisor_user_id=owner.user_id,
            deposit_amount=500,
            expires_at=date.today() + timedelta(days=7),
        ),
        owner,
    )
    with pytest.raises(InvalidVehicleStatusTransitionError):
        await service.update_vehicle(
            vehicle.id, VehicleUpdate(status=target, status_reason="Prueba de cambio"), owner
        )
    assert vehicle.status == VehicleStatus.RESERVADO


@pytest.mark.asyncio
async def test_manual_status_requires_reason_and_keeps_actor_timestamp(env):
    service, session, filial_id, vehicle, client = env
    owner = _user(filial_id=filial_id)
    for reason in (None, "  ", " x "):
        with pytest.raises(BadRequestError, match="motivo"):
            await service.update_vehicle(
                vehicle.id,
                VehicleUpdate(status=VehicleStatus.EN_PREPARACION, status_reason=reason),
                owner,
            )
    assert await service.list_status_history(vehicle.id) == []
    await service.update_vehicle(
        vehicle.id,
        VehicleUpdate(
            status=VehicleStatus.EN_PREPARACION, status_reason=" Inspección previa a entrega "
        ),
        owner,
    )
    history = await service.list_status_history(vehicle.id)
    assert len(history) == 1
    event = history[0]
    assert event.previous_status == "disponible"
    assert event.new_status == "en_preparacion"
    assert event.user_id == owner.user_id
    assert event.user_name == owner.email
    assert event.reason == "Inspección previa a entrega"
    assert event.created_at is not None
    with pytest.raises(InvalidVehicleStatusTransitionError):
        await service.update_vehicle(
            vehicle.id,
            VehicleUpdate(status=VehicleStatus.EN_TRANSITO, status_reason="Volver a tránsito"),
            owner,
        )


@pytest.mark.asyncio
async def test_reservation_snapshot_is_retained_after_release(env):
    service, session, filial_id, vehicle, client = env
    owner = _user(filial_id=filial_id)
    await service.reserve_vehicle(
        vehicle.id,
        VehicleReservationInput(
            client_id=client.id,
            advisor_user_id=owner.user_id,
            deposit_amount=500,
            expires_at=date.today() + timedelta(days=7),
            reason="Cliente solicita reserva",
        ),
        owner,
    )
    await service.update_vehicle(
        vehicle.id,
        VehicleUpdate(status=VehicleStatus.DISPONIBLE, status_reason="Cliente desiste de compra"),
        owner,
    )
    history = await service.list_status_history(vehicle.id)
    assert [e.new_status for e in history] == ["disponible", "reservado"]
    assert history[0].reservation_snapshot["client_id"] == str(client.id)
    assert history[0].reservation_snapshot["deposit_amount"] == 500
    assert history[1].reason == "Cliente solicita reserva"
    assert vehicle.reserved_client_id is None
    with pytest.raises(BadRequestError):
        await service.delete_vehicle(vehicle.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("admin", [False, True])
async def test_reserved_unit_cannot_be_sold_to_a_different_client_even_by_admin(env, admin):
    from sqlalchemy import select

    from app.modules.concesionario.models import VehicleSale
    from app.modules.concesionario.schemas import VehicleSaleInput

    service, session, filial_id, vehicle, client = env
    owner = _user(filial_id=filial_id)
    await service.reserve_vehicle(
        vehicle.id,
        VehicleReservationInput(
            client_id=client.id,
            advisor_user_id=owner.user_id,
            deposit_amount=500,
            expires_at=date.today() + timedelta(days=7),
        ),
        owner,
    )
    with pytest.raises(BadRequestError, match="otro cliente"):
        await service.update_vehicle(
            vehicle.id,
            VehicleUpdate(
                status=VehicleStatus.VENDIDO,
                sale=VehicleSaleInput(
                    client_id=uuid.uuid4(),
                    client_name="Otro cliente",
                    advisor_user_id=owner.user_id,
                    sale_type="contado",
                    payment_method="usd",
                ),
            ),
            _user(filial_id=filial_id, role_slug="filial-admin") if admin else owner,
        )
    assert vehicle.status == VehicleStatus.RESERVADO
    assert session.scalars(select(VehicleSale)).all() == []


@pytest.mark.asyncio
async def test_sale_retains_reserved_client_and_advisor_and_audits_sale(env):
    from sqlalchemy import select

    from app.modules.concesionario.models import VehicleSale
    from app.modules.concesionario.schemas import VehicleSaleInput

    service, session, filial_id, vehicle, client = env
    owner = _user(filial_id=filial_id)
    await service.reserve_vehicle(
        vehicle.id,
        VehicleReservationInput(
            client_id=client.id,
            advisor_user_id=owner.user_id,
            deposit_amount=500,
            expires_at=date.today() + timedelta(days=7),
        ),
        owner,
    )
    updated = await service.update_vehicle(
        vehicle.id,
        VehicleUpdate(
            status=VehicleStatus.VENDIDO,
            sale=VehicleSaleInput(
                client_id=client.id,
                client_name="Nombre alterado",
                advisor_user_id=owner.user_id,
                sale_type="contado",
                payment_method="usd",
            ),
        ),
        owner,
    )
    assert updated.status == VehicleStatus.VENDIDO
    sale = session.scalars(select(VehicleSale)).one()
    assert sale.client_name == client.full_name
    assert sale.advisor_user_id == owner.user_id
    event = (await service.list_status_history(vehicle.id))[0]
    assert event.new_status == "vendido"
    assert event.reservation_snapshot["client_id"] == str(client.id)
    assert "Venta CV-" in event.reason


@pytest.mark.asyncio
async def test_vehicle_creation_cannot_bypass_reservation_or_sale(env):
    from app.modules.concesionario.schemas import VehicleCreate

    service, session, filial_id, vehicle, client = env
    for status in (VehicleStatus.RESERVADO, VehicleStatus.VENDIDO):
        with pytest.raises(BadRequestError):
            await service.create_vehicle(
                VehicleCreate(
                    filial_id=filial_id,
                    status=status,
                    condition="nuevo",
                    location="patio",
                    brand="Toyota",
                    model="Hilux",
                    year=2026,
                    vin="12345678901234567",
                    sku="OTHER",
                    price_cash=20000,
                    price_financed=20000,
                    cost_price=18000,
                )
            )


@pytest.mark.asyncio
async def test_sale_failure_rolls_back_sale_income_and_keeps_reservation(env, monkeypatch):
    from sqlalchemy import select

    from app.modules.administracion.enums import AccountCurrency, AccountType
    from app.modules.administracion.models import Account, IncomeEntry
    from app.modules.concesionario.models import VehicleSale
    from app.modules.concesionario.schemas import VehicleSaleInput
    from app.modules.post_ventas.service import PostVentasService

    service, session, filial_id, vehicle, client = env
    owner = _user(filial_id=filial_id)
    await service.reserve_vehicle(
        vehicle.id,
        VehicleReservationInput(
            client_id=client.id,
            advisor_user_id=owner.user_id,
            deposit_amount=500,
            expires_at=date.today() + timedelta(days=7),
        ),
        owner,
    )
    session.add(
        Account(
            filial_id=filial_id,
            name="Caja",
            currency=AccountCurrency.USD,
            account_type=AccountType.CAJA,
        )
    )
    session.commit()

    async def fail(*args, **kwargs):
        raise RuntimeError("Fallo de garantía")

    monkeypatch.setattr(PostVentasService, "create_or_renew_warranty_from_sale", fail)
    with pytest.raises(RuntimeError):
        await service.update_vehicle(
            vehicle.id,
            VehicleUpdate(
                status=VehicleStatus.VENDIDO,
                sale=VehicleSaleInput(
                    client_id=client.id,
                    client_name=client.full_name,
                    advisor_user_id=owner.user_id,
                    sale_type="contado",
                    payment_method="usd",
                ),
            ),
            owner,
        )
    session.commit()
    assert session.scalars(select(VehicleSale)).all() == []
    assert session.scalars(select(IncomeEntry)).all() == []
    assert vehicle.status == VehicleStatus.RESERVADO
    assert [e.new_status for e in await service.list_status_history(vehicle.id)] == ["reservado"]


@pytest.mark.asyncio
async def test_http_status_changes_enforce_reason_and_expose_trace(env, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core.exception_handlers import register_exception_handlers
    from app.modules.auth.dependencies import get_current_user
    from app.modules.concesionario import router as routes

    service, session, filial_id, vehicle, client = env
    caller = _user(filial_id=filial_id, role_slug="filial-admin")
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: caller
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        missing_reason = await http.patch(
            f"/dealership-vehicles/{vehicle.id}", json={"status": "en_preparacion"}
        )
        assert missing_reason.status_code == 400
        assert vehicle.status == VehicleStatus.DISPONIBLE
        change = await http.patch(
            f"/dealership-vehicles/{vehicle.id}",
            json={"status": "en_preparacion", "status_reason": "Inspección de entrega"},
        )
        assert change.status_code == 200
        trace = await http.get(f"/dealership-vehicles/{vehicle.id}/status-history")
        assert trace.status_code == 200
        assert trace.json()[0]["user_id"] == str(caller.user_id)
        assert trace.json()[0]["reason"] == "Inspección de entrega"
        invalid = await http.patch(
            f"/dealership-vehicles/{vehicle.id}",
            json={"status": "en_transito", "status_reason": "Intento inválido"},
        )
        assert invalid.status_code != 200
        reserve_without_client = await http.post(
            f"/dealership-vehicles/{vehicle.id}/reserve",
            json={
                "advisor_user_id": str(caller.user_id),
                "deposit_amount": 100,
                "expires_at": date.today().isoformat(),
            },
        )
        assert reserve_without_client.status_code == 422
        # Audit access also honors filial isolation, even for an administrator.
        app.dependency_overrides[get_current_user] = lambda: caller.model_copy(
            update={"filial_id": uuid.uuid4()}
        )
        denied = await http.get(f"/dealership-vehicles/{vehicle.id}/status-history")
        assert denied.status_code == 403
