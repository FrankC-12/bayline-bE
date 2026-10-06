"""Cross-visit upsell resurfacing — a pending/postponed recommendation from
a past (likely already-closed) visit must still be discoverable and
applicable to a NEW ODS for the same vehicle, with its price frozen at
detection time (never recalculated on read), and applying it must price
fresh off today's rate/FIFO cost on the TARGET order, not the original."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter, make_order_type

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exceptions import BadRequestError
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.filiales.models import Filial
from app.modules.post_ventas.enums import TemparioCategory
from app.modules.post_ventas.models import LaborSettings, Tempario
from app.modules.service_orders.enums import ServiceOrderStatus, UpsellDiscardReason, UpsellSeverity, UpsellStatus
from app.modules.service_orders.exceptions import ServiceOrderReadOnlyError
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.schemas import UpsellCreate, UpsellDecisionInput, UpsellTaskInput
from app.modules.service_orders.service import ServiceOrderService


def _close(session, order):
    order.status = ServiceOrderStatus.ORDEN_CERRADA
    order.invoiced_at = datetime.now(UTC)
    session.commit()


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        settings = LaborSettings(filial_id=filial_id, hourly_rate=25, iva_percentage=16)
        session.add(settings)

        client = Client(
            filial_id=filial_id, full_name="Cliente", client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V, document_number="12345678", phone_primary="04121234567", address="Caracas",
        )
        session.add(client)
        session.commit()
        vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Corolla", plate="ABC123")
        other_vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Yaris", plate="XYZ999")
        session.add_all([vehicle, other_vehicle])
        session.commit()

        # The visit where the recommendation was first detected — starts
        # open (an upsell can only be created against an editable order);
        # individual tests close it afterward to simulate "that visit
        # ended, this one resurfaces at a later visit".
        order_type_id = make_order_type(session, filial_id)
        origin_order = ServiceOrder(
            filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id, advisor_user_id=uuid.uuid4(),
            order_type_id=order_type_id,
        )
        # Today's new visit for the same vehicle — open.
        new_order = ServiceOrder(
            filial_id=filial_id, sequence_number=2, vehicle_id=vehicle.id, advisor_user_id=uuid.uuid4(),
            order_type_id=order_type_id,
        )
        # A visit for a DIFFERENT vehicle — open, used to prove cross-vehicle
        # apply is rejected.
        other_vehicle_order = ServiceOrder(
            filial_id=filial_id, sequence_number=3, vehicle_id=other_vehicle.id, advisor_user_id=uuid.uuid4(),
            order_type_id=order_type_id,
        )
        session.add_all([origin_order, new_order, other_vehicle_order])
        session.commit()

        tempario = Tempario(
            filial_id=filial_id, category=TemparioCategory.FRENOS, sequence_number=1,
            name="Cambio de pastillas", estimated_hours=2,
        )
        session.add(tempario)
        session.commit()

        db = AsyncAdapter(session)
        yield ServiceOrderService(db), session, settings, origin_order, new_order, other_vehicle_order, tempario


@pytest.mark.asyncio
async def test_quoted_price_is_frozen_and_survives_a_later_rate_change(env):
    service, session, settings, origin_order, _new_order, _other, tempario = env
    upsell = await service.create_upsell(
        origin_order.id,
        UpsellCreate(
            title="Frenos desgastados", description="Se notó desgaste.",
            severity=UpsellSeverity.PRONTO, tasks=[UpsellTaskInput(tempario_id=tempario.id)],
        ),
        [],
    )
    assert upsell.amount == pytest.approx(50.0)  # 2h * $25

    settings.hourly_rate = 100
    session.commit()

    reread = await service.get_upsell(upsell.id)
    assert reread.amount == pytest.approx(50.0)  # unchanged despite the rate hike


@pytest.mark.asyncio
async def test_discard_requires_a_reason(env):
    service, _session, _settings, origin_order, _new_order, _other, tempario = env
    upsell = await service.create_upsell(
        origin_order.id,
        UpsellCreate(
            title="Recomendación", description="Descripción de prueba.",
            severity=UpsellSeverity.MONITOREAR, tasks=[UpsellTaskInput(tempario_id=tempario.id)],
        ),
        [],
    )
    with pytest.raises(ValidationError):
        UpsellDecisionInput(status="rechazado")
    with pytest.raises(ValidationError):
        UpsellDecisionInput(status="rechazado", discard_reason=UpsellDiscardReason.OTRO)
    # Sanity: a valid discard still works against this fixture's upsell.
    decided = await service.decide_upsell(
        upsell.id, UpsellDecisionInput(status="rechazado", discard_reason=UpsellDiscardReason.CLIENTE_NO_LO_QUIERE), uuid.uuid4()
    )
    assert decided.status == UpsellStatus.RECHAZADO
    assert decided.discard_reason == UpsellDiscardReason.CLIENTE_NO_LO_QUIERE


@pytest.mark.asyncio
async def test_apply_to_a_later_order_prices_fresh_even_though_origin_is_closed(env):
    service, session, settings, origin_order, new_order, _other, tempario = env
    upsell = await service.create_upsell(
        origin_order.id,
        UpsellCreate(
            title="Frenos desgastados", description="Se notó desgaste.",
            severity=UpsellSeverity.URGENTE, tasks=[UpsellTaskInput(tempario_id=tempario.id)],
        ),
        [],
    )
    assert upsell.status == UpsellStatus.PENDIENTE

    # The visit ends and the rate moves before the vehicle comes back.
    _close(session, origin_order)
    settings.hourly_rate = 40
    session.commit()

    decided = await service.decide_upsell(
        upsell.id,
        UpsellDecisionInput(status="aprobado", approval_channel="presencial", target_service_order_id=new_order.id),
        uuid.uuid4(),
    )
    assert decided.applied_to_service_order_id == new_order.id

    # Nothing landed on the (closed) origin order.
    assert await service.list_tasks(origin_order.id) == []
    tasks = await service.list_tasks(new_order.id)
    assert len(tasks) == 1
    assert tasks[0].tempario_id == tempario.id

    summary = await service.get_order_summary(new_order.id)
    # 2h * $40 (today's rate) + 16% IVA — never the $25 rate in effect when detected.
    assert summary.total == pytest.approx(80 * 1.16)


@pytest.mark.asyncio
async def test_apply_to_an_order_of_a_different_vehicle_is_rejected(env):
    service, _session, _settings, origin_order, _new_order, other_vehicle_order, tempario = env
    upsell = await service.create_upsell(
        origin_order.id,
        UpsellCreate(
            title="Recomendación", description="Descripción de prueba.",
            severity=UpsellSeverity.MONITOREAR, tasks=[UpsellTaskInput(tempario_id=tempario.id)],
        ),
        [],
    )
    with pytest.raises(BadRequestError):
        await service.decide_upsell(
            upsell.id,
            UpsellDecisionInput(
                status="aprobado", approval_channel="presencial", target_service_order_id=other_vehicle_order.id
            ),
            uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_apply_to_a_closed_target_order_is_rejected(env):
    service, session, _settings, origin_order, _new_order, _other, tempario = env
    upsell = await service.create_upsell(
        origin_order.id,
        UpsellCreate(
            title="Recomendación", description="Descripción de prueba.",
            severity=UpsellSeverity.MONITOREAR, tasks=[UpsellTaskInput(tempario_id=tempario.id)],
        ),
        [],
    )
    _close(session, origin_order)

    with pytest.raises(ServiceOrderReadOnlyError):
        await service.decide_upsell(
            upsell.id,
            UpsellDecisionInput(status="aprobado", approval_channel="presencial", target_service_order_id=origin_order.id),
            uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_list_pending_upsells_for_vehicle_excludes_current_order_and_resolved(env):
    service, session, _settings, origin_order, new_order, other_vehicle_order, tempario = env
    pending = await service.create_upsell(
        origin_order.id,
        UpsellCreate(
            title="Pendiente", description="Descripción de prueba.",
            severity=UpsellSeverity.URGENTE, tasks=[UpsellTaskInput(tempario_id=tempario.id)],
        ),
        [],
    )
    postponed = await service.create_upsell(
        origin_order.id,
        UpsellCreate(
            title="Pospuesta", description="Descripción de prueba.",
            severity=UpsellSeverity.PRONTO, tasks=[UpsellTaskInput(tempario_id=tempario.id)],
        ),
        [],
    )
    resolved = await service.create_upsell(
        origin_order.id,
        UpsellCreate(
            title="Ya decidida", description="Descripción de prueba.",
            severity=UpsellSeverity.MONITOREAR, tasks=[UpsellTaskInput(tempario_id=tempario.id)],
        ),
        [],
    )
    same_order_pending = await service.create_upsell(
        new_order.id,
        UpsellCreate(
            title="De esta misma visita", description="Descripción de prueba.",
            severity=UpsellSeverity.MONITOREAR, tasks=[UpsellTaskInput(tempario_id=tempario.id)],
        ),
        [],
    )
    await service.decide_upsell(postponed.id, UpsellDecisionInput(status="pospuesto"), uuid.uuid4())
    await service.decide_upsell(
        resolved.id, UpsellDecisionInput(status="rechazado", discard_reason=UpsellDiscardReason.YA_NO_APLICA), uuid.uuid4()
    )
    _close(session, origin_order)

    results = await service.list_pending_upsells_for_vehicle(
        origin_order.vehicle_id, exclude_service_order_id=new_order.id
    )
    result_ids = {r.id for r in results}

    assert pending.id in result_ids
    assert postponed.id in result_ids
    assert resolved.id not in result_ids
    assert same_order_pending.id not in result_ids
    assert all(r.origin_service_order_code == origin_order.code for r in results)

    # A different vehicle's own pending upsells never leak in.
    other_results = await service.list_pending_upsells_for_vehicle(other_vehicle_order.vehicle_id)
    assert other_results == []
