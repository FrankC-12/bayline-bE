"""Regression coverage for the October mobile handoff, against actual services."""

import uuid
from datetime import UTC, datetime

import pytest
import test_completion_requires_finished_work as work_fixtures
import test_inspection_notes_required as inspection_fixtures
import test_pending_upsells_cross_visit as visit_fixtures
import test_task_timer as timer_fixtures

work_env = work_fixtures.env
inspection_env = inspection_fixtures.env
visit_env = visit_fixtures.env
inventory = timer_fixtures.inventory
order_inventory = timer_fixtures.order_inventory
_add_task = timer_fixtures._add_task


from app.core.exceptions import ConflictError
from app.core.venezuela_time import caracas_date
from app.modules.inspections.schemas import InspectionCreate
from app.modules.service_orders.enums import TaskStatus
from app.modules.service_orders.schemas import UpsellCreate, UpsellTaskInput


@pytest.mark.asyncio
async def test_task_and_timer_cannot_bypass_unreceived_parts(work_env):
    service, order, tempario, _user = work_env
    task = await service.add_task(order.id, tempario.id)
    for operation in (
        lambda: service.update_task_status(task.id, TaskStatus.EN_PROGRESO),
        lambda: service.start_task_timer(task.id),
    ):
        with pytest.raises(ConflictError) as error:
            await operation()
        assert error.value.error_code == "task_parts_not_received"
    assert task.status == TaskStatus.PENDIENTE
    assert task.timer_started_at is None


@pytest.mark.asyncio
async def test_pause_stores_reason_history_and_task_completion_stops_timer(order_inventory):
    service, _session, order, _part, _lots = order_inventory
    task = await _add_task(service, order)
    await service.start_task_timer(task.id)
    await service.pause_task_timer(task.id, "almuerzo", "Regreso a las 13:00")
    assert task.pause_reason == "almuerzo"
    assert task.pause_history[0]["notes"] == "Regreso a las 13:00"
    await service.start_task_timer(task.id)
    await service.update_task_status(task.id, TaskStatus.COMPLETADA)
    assert task.timer_started_at is None


@pytest.mark.asyncio
async def test_only_one_running_timer_across_orders_for_the_same_technician(visit_env):
    service, session, _settings, first_order, second_order, _other, tempario = visit_env
    technician = uuid.uuid4()
    first_order.technician_user_id = second_order.technician_user_id = technician
    session.commit()
    first = await service.add_task(first_order.id, tempario.id)
    second = await service.add_task(second_order.id, tempario.id)
    await service.start_task_timer(first.id)
    with pytest.raises(ConflictError) as error:
        await service.start_task_timer(second.id)
    assert error.value.error_code == "technician_timer_running"
    await service.pause_task_timer(first.id, "otra_tarea")
    await service.start_task_timer(second.id)
    assert second.timer_started_at is not None


@pytest.mark.asyncio
async def test_decreasing_mileage_requires_and_persists_a_correction_reason(inspection_env):
    inspections, _session, filial, vehicle = inspection_env
    first = await inspections.create_inspection(
        InspectionCreate(
            filial_id=filial, vehicle_id=vehicle, mileage=200000, notes="Primera visita"
        ),
        uuid.uuid4(),
    )
    with pytest.raises(ConflictError) as error:
        await inspections.create_inspection(
            InspectionCreate(
                filial_id=filial, vehicle_id=vehicle, mileage=20000, notes="Otra visita"
            ),
            uuid.uuid4(),
        )
    assert error.value.error_code == "mileage_inconsistent"
    corrected = await inspections.create_inspection(
        InspectionCreate(
            filial_id=filial,
            vehicle_id=vehicle,
            mileage=20000,
            notes="Lectura verificada",
            mileage_correction_reason="Error de digitación anterior; foto revisada",
        ),
        uuid.uuid4(),
    )
    assert corrected.mileage == 20000
    assert corrected.mileage_correction_reason
    assert first.mileage == 200000  # History remains intact.


def test_last_visit_date_uses_caracas_even_when_utc_is_tomorrow():
    assert caracas_date(datetime(2026, 10, 14, 2, 31, tzinfo=UTC)).isoformat() == "2026-10-13"
    assert caracas_date(datetime(2026, 10, 14, 2, 31)).isoformat() == "2026-10-13"


@pytest.mark.asyncio
async def test_current_recommendation_quote_does_not_overwrite_original_price(
    visit_env, monkeypatch
):
    from app.modules.service_orders import router

    service, session, settings, origin, _new, _other, tempario = visit_env
    finding = await service.create_upsell(
        origin.id,
        UpsellCreate(
            title="Servicio recomendado",
            description="Revisar frenos",
            severity="pronto",
            tasks=[UpsellTaskInput(tempario_id=tempario.id)],
        ),
        [],
    )
    original = finding.amount
    settings.hourly_rate = 50
    session.commit()

    async def allow(*args, **kwargs):
        pass

    monkeypatch.setattr(router, "_ensure_access", allow)
    quote = await router.quote_upsell(finding.id, None, service)
    assert quote["labor"] == 100
    assert quote["total"] == 116
    assert (await service.get_upsell(finding.id)).amount == original


@pytest.mark.asyncio
async def test_creating_order_applies_accepted_recommendation_with_current_prices(visit_env):
    from app.modules.service_orders.schemas import AcceptedUpsellInput, ServiceOrderCreate

    service, session, settings, origin, _new, _other, tempario = visit_env
    finding = await service.create_upsell(
        origin.id,
        UpsellCreate(
            title="Trabajo futuro",
            description="Revisar frenos",
            severity="pronto",
            tasks=[UpsellTaskInput(tempario_id=tempario.id)],
        ),
        [],
    )
    settings.hourly_rate = 50
    session.commit()
    created = await service.create_order(
        ServiceOrderCreate(
            filial_id=origin.filial_id,
            vehicle_id=origin.vehicle_id,
            order_type_id=origin.order_type_id,
            advisor_user_id=origin.advisor_user_id,
            customer_reason="Mantenimiento aceptado por el cliente",
            scheduled_at=datetime(2026, 11, 1, 12, tzinfo=UTC),
            accepted_upsells=[
                AcceptedUpsellInput(upsell_id=finding.id, approval_channel="presencial")
            ],
        )
    )
    assert created.customer_reason == "Mantenimiento aceptado por el cliente"
    assert len(await service.list_tasks(created.id)) == 1
    assert (await service.get_upsell(finding.id)).applied_to_service_order_id == created.id
    assert (await service.get_order_summary(created.id)).total == 116


@pytest.mark.asyncio
async def test_removing_last_part_does_not_leave_a_phantom_pending_odt(work_env):
    service, order, tempario, _user = work_env
    task = await service.add_task(order.id, tempario.id)
    await service.update_task_status(task.id, TaskStatus.COMPLETADA)
    transfer = (await service.list_transfers(order.id))[0]
    assert order.completed_with_pending_items
    await service.remove_transfer_line(transfer.lines[0].id)
    assert await service.list_transfers(order.id) == []
    assert (await service.get_order_summary(order.id)).transfers == []
    assert order.completed_with_pending_items is False
