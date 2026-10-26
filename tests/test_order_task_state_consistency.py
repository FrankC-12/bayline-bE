"""Order state must reflect the tasks that actually remain after each edit."""

import pytest
from workshop_ready import task_ready
from test_completion_requires_finished_work import env

from app.modules.service_orders.enums import ServiceOrderStatus, TaskStatus

__all__ = ["env"]


@pytest.mark.asyncio
async def test_deleting_unfinished_task_completes_remaining_finished_work(env):
    service, order, tempario, _user = env
    finished = await service.add_task(order.id, tempario.id)
    unfinished = await service.add_task(order.id, tempario.id)
    await task_ready(service, order, finished)
    await service.update_task_status(finished.id, TaskStatus.COMPLETADA)
    assert order.status == ServiceOrderStatus.EN_PROGRESO

    await service.delete_task(unfinished.id)

    assert [t.id for t in await service.list_tasks(order.id)] == [finished.id]
    assert order.status == ServiceOrderStatus.COMPLETADO
    assert order.completed_at is not None
    assert order.completed_with_pending_items is False


@pytest.mark.asyncio
async def test_deleting_only_finished_task_restores_empty_order_to_pending(env):
    service, order, tempario, _user = env
    task = await service.add_task(order.id, tempario.id)
    await task_ready(service, order, task)
    await service.update_task_status(task.id, TaskStatus.COMPLETADA)

    await service.delete_task(task.id)

    assert await service.list_tasks(order.id) == []
    assert order.status == ServiceOrderStatus.PENDIENTE
    assert order.completed_at is None
    assert order.completed_with_pending_items is False


@pytest.mark.asyncio
async def test_adding_work_clears_completion_until_new_work_finishes(env):
    service, order, tempario, _user = env
    task = await service.add_task(order.id, tempario.id)
    await task_ready(service, order, task)
    await service.update_task_status(task.id, TaskStatus.COMPLETADA)
    assert order.completed_at is not None

    extra = await service.add_task(order.id, tempario.id)

    assert order.status == ServiceOrderStatus.EN_PROGRESO
    assert order.completed_at is None
    assert order.completed_with_pending_items is False

    await task_ready(service, order, extra)
    await service.update_task_status(extra.id, TaskStatus.COMPLETADA)

    assert order.status == ServiceOrderStatus.COMPLETADO
    assert order.completed_at is not None
    assert order.completed_with_pending_items is False
