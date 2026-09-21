"""Vehicle status transitions are now a defined graph (not a blind field
assignment), and reserving requires a client, salesperson, deposit and
validity date, locking the unit against every other salesperson until it's
released or sold."""

import uuid
from datetime import date, timedelta

import pytest
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
        user_id=user_id or uuid.uuid4(), email="user@test.com", role_id=uuid.uuid4(), role_slug=role_slug,
        scope=RoleScope.FILIAL, holding_id=None, filial_id=filial_id,
    )


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Concesionario", slug="conce"))
        client = Client(
            filial_id=filial_id, full_name="Cliente", client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V, document_number="12345678", phone_primary="04121234567", address="Caracas",
        )
        session.add(client)
        session.commit()
        vehicle = DealershipVehicle(
            filial_id=filial_id, status=VehicleStatus.DISPONIBLE, condition=VehicleCondition.NUEVO,
            brand="Toyota", model="Corolla", year=2026, vin=str(uuid.uuid4())[:17], sku="SKU-1",
            price_cash=10_000, price_financed=11_000, cost_price=8_000,
        )
        session.add(vehicle)
        session.commit()
        db = AsyncAdapter(session)
        yield ConcesionarioService(db), session, filial_id, vehicle, client


@pytest.mark.asyncio
async def test_reserving_requires_client_deposit_and_validity(env):
    service, _session, _filial_id, vehicle, _client = env
    with pytest.raises(Exception):
        VehicleReservationInput(advisor_user_id=uuid.uuid4(), deposit_amount=0, expires_at=date.today())


@pytest.mark.asyncio
async def test_reserve_vehicle_locks_it_to_the_chosen_advisor(env):
    service, session, filial_id, vehicle, client = env
    advisor_id = uuid.uuid4()
    caller = _user(filial_id=filial_id, user_id=advisor_id)

    reserved = await service.reserve_vehicle(
        vehicle.id,
        VehicleReservationInput(
            client_id=client.id, advisor_user_id=advisor_id, deposit_amount=500,
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
                client_id=client.id, advisor_user_id=caller.user_id, deposit_amount=500,
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
            client_id=client.id, advisor_user_id=owner.user_id, deposit_amount=500,
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
                client_id=client.id, advisor_user_id=stranger.user_id, deposit_amount=100,
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
            client_id=client.id, advisor_user_id=owner.user_id, deposit_amount=500,
            expires_at=date.today() + timedelta(days=7),
        ),
        owner,
    )

    released = await service.update_vehicle(
        vehicle.id, VehicleUpdate(status=VehicleStatus.DISPONIBLE), admin
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
            client_id=client.id, advisor_user_id=owner.user_id, deposit_amount=500,
            expires_at=date.today() + timedelta(days=7),
        ),
        owner,
    )

    released = await service.update_vehicle(
        vehicle.id, VehicleUpdate(status=VehicleStatus.DISPONIBLE), owner
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
                    client_id=client.id, advisor_user_id=caller.user_id, deposit_amount=500,
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
        await service.update_vehicle(vehicle.id, VehicleUpdate(status=VehicleStatus.RESERVADO), caller)
