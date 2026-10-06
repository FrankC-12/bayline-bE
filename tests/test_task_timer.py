"""Manual cronómetro on a ServiceOrderTask — start/pause, independent of the
task's status. timer_started_at marks the currently-running segment (None
while paused); timer_accumulated_seconds sums every previously-closed
segment, so pausing and resuming multiple times adds up correctly."""

from datetime import UTC, datetime, timedelta

import pytest
from test_part_sales_fifo import inventory as fifo_inventory
from test_parts_discounts import order_inventory as discount_order_inventory

from app.modules.post_ventas.enums import TemparioCategory
from app.modules.post_ventas.models import Tempario
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.exceptions import (
    ServiceOrderReadOnlyError,
    TaskTimerAlreadyRunningError,
    TaskTimerNotRunningError,
)

inventory = fifo_inventory
order_inventory = discount_order_inventory


async def _add_task(service, order):
    tempario = Tempario(
        filial_id=order.filial_id, category=TemparioCategory.MOTOR, sequence_number=1,
        name="Cambio de aceite", estimated_hours=1,
    )
    service.db.add(tempario)
    await service.db.commit()
    return await service.add_task(order.id, tempario.id)


@pytest.mark.asyncio
async def test_starting_the_timer_sets_started_at(order_inventory):
    service, _session, order, _part_id, _lots = order_inventory
    task = await _add_task(service, order)
    assert task.timer_started_at is None
    assert task.timer_accumulated_seconds == 0

    started = await service.start_task_timer(task.id)
    assert started.timer_started_at is not None
    assert started.timer_accumulated_seconds == 0


@pytest.mark.asyncio
async def test_starting_an_already_running_timer_raises(order_inventory):
    service, _session, order, _part_id, _lots = order_inventory
    task = await _add_task(service, order)
    await service.start_task_timer(task.id)
    with pytest.raises(TaskTimerAlreadyRunningError):
        await service.start_task_timer(task.id)


@pytest.mark.asyncio
async def test_pausing_accumulates_elapsed_time_and_clears_started_at(order_inventory):
    service, session, order, _part_id, _lots = order_inventory
    task = await _add_task(service, order)
    await service.start_task_timer(task.id)
    # Simulate 90 elapsed seconds without sleeping in the test.
    task.timer_started_at = datetime.now(UTC) - timedelta(seconds=90)
    session.commit()

    paused = await service.pause_task_timer(task.id)
    assert paused.timer_started_at is None
    assert paused.timer_accumulated_seconds >= 90


@pytest.mark.asyncio
async def test_pausing_and_resuming_adds_up_accumulated_seconds(order_inventory):
    service, session, order, _part_id, _lots = order_inventory
    task = await _add_task(service, order)
    await service.start_task_timer(task.id)
    task.timer_started_at = datetime.now(UTC) - timedelta(seconds=60)
    session.commit()
    await service.pause_task_timer(task.id)

    await service.start_task_timer(task.id)
    task.timer_started_at = datetime.now(UTC) - timedelta(seconds=30)
    session.commit()
    resumed = await service.pause_task_timer(task.id)

    assert resumed.timer_accumulated_seconds >= 90


@pytest.mark.asyncio
async def test_pausing_a_timer_that_isnt_running_raises(order_inventory):
    service, _session, order, _part_id, _lots = order_inventory
    task = await _add_task(service, order)
    with pytest.raises(TaskTimerNotRunningError):
        await service.pause_task_timer(task.id)


@pytest.mark.asyncio
async def test_timer_cannot_be_started_on_a_closed_order(order_inventory):
    service, session, order, _part_id, _lots = order_inventory
    task = await _add_task(service, order)
    order.status = ServiceOrderStatus.ORDEN_CERRADA
    session.commit()
    with pytest.raises(ServiceOrderReadOnlyError):
        await service.start_task_timer(task.id)
