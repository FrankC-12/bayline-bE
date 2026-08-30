import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.schemas import (
    BayCreate,
    BayRead,
    BayUpdate,
    OrderSummary,
    ServiceOrderCreate,
    ServiceOrderRead,
    ServiceOrderUpdate,
    TaskCreate,
    TaskRead,
    TaskStatusUpdate,
    TransferLineInput,
    TransferRead,
    UpsellCreate,
    UpsellRead,
    UpsellStatusUpdate,
)
from app.modules.service_orders.service import ACTIVE_STATUSES, HISTORY_STATUSES, ServiceOrderService

MODULE_ID = "asesor-servicios"

router = APIRouter(tags=["Service Orders"])


def get_service(db: AsyncSession = Depends(get_db)) -> ServiceOrderService:
    return ServiceOrderService(db)


async def _ensure_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, MODULE_ID, level)


@router.get("/service-orders", response_model=list[ServiceOrderRead])
async def list_service_orders(
    filial_id: uuid.UUID = Query(...),
    view: str = Query(default="active", pattern="^(active|history|all)$"),
    date: dt.date | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> list[ServiceOrderRead]:
    """List service orders. 'active' = kanban statuses, 'history' = closed/cancelled.
    Pass 'date' to filter by scheduled_at (used by the Calendario view)."""
    await _ensure_access(current_user, filial_id, service.db)
    statuses: list[ServiceOrderStatus] | None
    if view == "active":
        statuses = ACTIVE_STATUSES
    elif view == "history":
        statuses = HISTORY_STATUSES
    else:
        statuses = None
    return await service.list_orders(filial_id, statuses, date)


@router.get("/service-orders/{order_id}", response_model=ServiceOrderRead)
async def get_service_order(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> ServiceOrderRead:
    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db)
    return order


@router.post("/service-orders", response_model=ServiceOrderRead, status_code=status.HTTP_201_CREATED)
async def create_service_order(
    payload: ServiceOrderCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> ServiceOrderRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_order(payload)


@router.patch("/service-orders/{order_id}", response_model=ServiceOrderRead)
async def update_service_order(
    order_id: uuid.UUID,
    payload: ServiceOrderUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> ServiceOrderRead:
    existing = await service.get_order(order_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_order(order_id, payload)


@router.delete("/service-orders/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service_order(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> None:
    existing = await service.get_order(order_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    await service.delete_order(order_id)


@router.get("/bays", response_model=list[BayRead])
async def list_bays(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> list[BayRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_bays(filial_id)


@router.post("/bays", response_model=BayRead, status_code=status.HTTP_201_CREATED)
async def create_bay(
    payload: BayCreate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> BayRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_bay(filial_id, payload)


@router.patch("/bays/{bay_id}", response_model=BayRead)
async def update_bay(
    bay_id: uuid.UUID,
    payload: BayUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> BayRead:
    existing = await service.get_bay(bay_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_bay(bay_id, payload)


@router.get("/service-orders/{order_id}/summary", response_model=OrderSummary)
async def get_order_summary(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> OrderSummary:
    """Tasks, ODTs and the live pricing summary for a service order — everything
    the detail screen needs in one call."""
    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db)
    return await service.get_order_summary(order_id)


@router.get("/service-orders/{order_id}/tasks", response_model=list[TaskRead])
async def list_tasks(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> list[TaskRead]:
    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db)
    tasks = await service.list_tasks(order_id)
    return [
        TaskRead(
            id=t.id,
            tempario_id=t.tempario_id,
            code_snapshot=t.code_snapshot,
            name_snapshot=t.name_snapshot,
            hours_snapshot=float(t.hours_snapshot),
            status=t.status,
            created_at=t.created_at,
        )
        for t in tasks
    ]


@router.post("/service-orders/{order_id}/tasks", status_code=status.HTTP_201_CREATED)
async def add_task(
    order_id: uuid.UUID,
    payload: TaskCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> OrderSummary:
    """Adds a tempario as a task on this order and returns the refreshed summary
    (the tempario's linked parts may have just been added to a pending ODT)."""
    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    await service.add_task(order_id, payload.tempario_id)
    return await service.get_order_summary(order_id)


@router.patch("/service-order-tasks/{task_id}", response_model=TaskRead)
async def update_task_status(
    task_id: uuid.UUID,
    payload: TaskStatusUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> TaskRead:
    filial_id = await service.get_task_filial(task_id)
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    task = await service.update_task_status(task_id, payload.status)
    return TaskRead(
        id=task.id,
        tempario_id=task.tempario_id,
        code_snapshot=task.code_snapshot,
        name_snapshot=task.name_snapshot,
        hours_snapshot=float(task.hours_snapshot),
        status=task.status,
        created_at=task.created_at,
    )


@router.delete("/service-order-tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> None:
    filial_id = await service.get_task_filial(task_id)
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    await service.delete_task(task_id)


@router.post("/service-orders/{order_id}/transfers/lines", status_code=status.HTTP_201_CREATED)
async def add_transfer_line(
    order_id: uuid.UUID,
    payload: TransferLineInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> OrderSummary:
    """Adds a part line to the order's pending ODT (creating one if needed) and
    returns the refreshed summary."""
    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    await service.add_transfer_line(order_id, payload.part_id, payload.quantity)
    return await service.get_order_summary(order_id)


@router.post("/service-order-transfers/{transfer_id}/mark-ordered", response_model=TransferRead)
async def mark_transfer_ordered(
    transfer_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> TransferRead:
    """Marks the ODT as 'Pedido' and decrements stock for every line in it."""
    filial_id = await service.get_transfer_filial(transfer_id)
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    transfer = await service.mark_transfer_ordered(transfer_id, current_user.user_id)
    return TransferRead(
        id=transfer.id,
        code=transfer.code,
        status=transfer.status,
        lines=[
            {
                "id": line.id,
                "part_id": line.part_id,
                "quantity": line.quantity,
                "unit_price": float(line.unit_price),
                "subtotal": line.quantity * float(line.unit_price),
            }
            for line in transfer.lines
        ],
        subtotal=sum(line.quantity * float(line.unit_price) for line in transfer.lines),
        fulfilled_by_user_id=transfer.fulfilled_by_user_id,
        fulfilled_at=transfer.fulfilled_at,
        created_at=transfer.created_at,
    )


@router.get("/upsells", response_model=list[UpsellRead])
async def list_upsells(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> list[UpsellRead]:
    """All upsells across every ODS in the filial — vehicle/technician info is
    resolved on the frontend, same as the main Órdenes de Servicio list."""
    await _ensure_access(current_user, filial_id, service.db)
    upsells = await service.list_upsells(filial_id)
    return [
        UpsellRead(
            id=u.id,
            service_order_id=u.service_order_id,
            title=u.title,
            description=u.description,
            detected_by_user_id=u.detected_by_user_id,
            evidence_count=u.evidence_count,
            status=u.status,
            created_at=u.created_at,
            resolved_at=u.resolved_at,
        )
        for u in upsells
    ]


@router.post(
    "/service-orders/{order_id}/upsells", response_model=UpsellRead, status_code=status.HTTP_201_CREATED
)
async def create_upsell(
    order_id: uuid.UUID,
    payload: UpsellCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> UpsellRead:
    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    u = await service.create_upsell(order_id, payload)
    return UpsellRead(
        id=u.id,
        service_order_id=u.service_order_id,
        title=u.title,
        description=u.description,
        detected_by_user_id=u.detected_by_user_id,
        evidence_count=u.evidence_count,
        status=u.status,
        created_at=u.created_at,
        resolved_at=u.resolved_at,
    )


@router.patch("/upsells/{upsell_id}", response_model=UpsellRead)
async def update_upsell_status(
    upsell_id: uuid.UUID,
    payload: UpsellStatusUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> UpsellRead:
    existing = await service.get_upsell(upsell_id)
    order = await service.get_order(existing.service_order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    u = await service.update_upsell_status(upsell_id, payload.status)
    return UpsellRead(
        id=u.id,
        service_order_id=u.service_order_id,
        title=u.title,
        description=u.description,
        detected_by_user_id=u.detected_by_user_id,
        evidence_count=u.evidence_count,
        status=u.status,
        created_at=u.created_at,
        resolved_at=u.resolved_at,
    )