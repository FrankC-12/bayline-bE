import datetime as dt
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.exceptions import RequestValidationError
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.pagination import Page
from app.core.storage import save_upload_attachment, save_upload_image
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.clients.schemas import ServiceVehicleRead
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access
from app.modules.service_orders.access import (
    require_list_access as _ensure_list_access,
)
from app.modules.service_orders.access import (
    require_order_access as _ensure_order_access,
)
from app.modules.service_orders.billing_schemas import (
    BillingInput,
    CollectInvoicePaymentInput,
    InvoiceCreate,
    ReceivableRead,
)
from app.modules.service_orders.enums import (
    ReworkFailureCategory,
    ServiceOrderStatus,
    UpsellSeverity,
    WarrantyClaimStatus,
    WarrantyClaimType,
)
from app.modules.service_orders.exceptions import TaskAndTechnicianRequiredError
from app.modules.service_orders.schemas import (
    BayCreate,
    BayRead,
    BayUpdate,
    ClaimReviewInput,
    OrderSummary,
    PendingUpsellRead,
    ServiceOrderCancelInput,
    ServiceOrderCloseInput,
    ServiceOrderCreate,
    ServiceOrderDraftCreate,
    ServiceOrderDraftUpdate,
    ServiceOrderPageItem,
    ServiceOrderRead,
    ServiceOrderTypeCreate,
    ServiceOrderTypeRead,
    ServiceOrderTypeUpdate,
    ServiceOrderUpdate,
    TaskCreate,
    TaskNotesInput,
    TaskPauseInput,
    TaskPayerUpdate,
    TaskRead,
    TaskStatusUpdate,
    TransferLineInput,
    TransferLinePayerUpdate,
    TransferLineQuantityUpdate,
    TransferNotReceivedInput,
    TransferRead,
    UpsellCreate,
    UpsellDecisionInput,
    UpsellPartInput,
    UpsellRead,
    UpsellTaskInput,
    WarrantyClaimAuthorizationInput,
    WarrantyClaimContext,
    WarrantyClaimConvertInput,
    WarrantyClaimCreate,
    WarrantyClaimRead,
)
from app.modules.service_orders.service import (
    ACTIVE_STATUSES,
    HISTORY_STATUSES,
    ServiceOrderService,
)

MODULE_ID = "asesor-servicios"
router = APIRouter(tags=["Service Orders"])


def _validated_form(model, **values):
    try:
        return model(**values)
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from error


def _form_items(model, value, field):
    try:
        return TypeAdapter(list[model]).validate_json(value)
    except ValidationError as error:
        raise RequestValidationError(
            [{**item, "loc": ("body", field, *item["loc"])} for item in error.errors()]
        ) from error


def get_service(db: AsyncSession = Depends(get_db)) -> ServiceOrderService:
    return ServiceOrderService(db)


async def _ensure_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, MODULE_ID, level)


# Collecting a receivable is a sensitive Finanzas action — gated by its own
# fine-grained module, separate from general "asesor-servicios" access
# (which still covers just seeing what's owed, via list_receivables below).
COBRAR_MODULE_ID = "finanzas-cobrar"


async def _ensure_cobrar_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, COBRAR_MODULE_ID, level)


# Managing the "tipos de ODS" catalog (create/rename/deactivate) is a
# Postventas admin action — gated by that module, separate from
# asesor-servicios, which still just needs to LIST them (via _ensure_access
# below) to populate the "tipo de orden" picker on a new ODS.
ORDER_TYPES_MODULE_ID = "ajustes"


async def _ensure_order_types_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, ORDER_TYPES_MODULE_ID, level)


@router.get("/service-order-types", response_model=list[ServiceOrderTypeRead])
async def list_order_types(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> list[ServiceOrderTypeRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_order_types(filial_id)


@router.post(
    "/service-order-types",
    response_model=ServiceOrderTypeRead,
    status_code=status.HTTP_201_CREATED,
    deprecated=True,
    include_in_schema=False,
)
async def create_order_type(
    payload: ServiceOrderTypeCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> ServiceOrderTypeRead:
    await _ensure_order_types_access(
        current_user, payload.filial_id, service.db, AccessLevel.EDITAR
    )
    return await service.create_order_type(payload.filial_id, payload)


@router.patch(
    "/service-order-types/{order_type_id}",
    response_model=ServiceOrderTypeRead,
    deprecated=True,
    include_in_schema=False,
)
async def update_order_type(
    order_type_id: uuid.UUID,
    payload: ServiceOrderTypeUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> ServiceOrderTypeRead:
    existing = await service.get_order_type(order_type_id)
    await _ensure_order_types_access(
        current_user, existing.filial_id, service.db, AccessLevel.EDITAR
    )
    return await service.update_order_type(order_type_id, payload)


@router.get("/service-orders", response_model=list[ServiceOrderRead])
async def list_service_orders(
    filial_id: uuid.UUID = Query(...),
    view: str = Query(default="active", pattern="^(active|history|all)$"),
    date: dt.date | None = Query(default=None),
    include_drafts: bool = Query(False),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> list[ServiceOrderRead]:
    """List service orders. 'active' = kanban statuses, 'history' = closed/cancelled.
    Pass 'date' to filter by scheduled_at (used by the Calendario view). A
    técnico without general asesor-servicios access only gets the orders
    assigned to them, instead of the full filial list."""
    technician_user_id = await _ensure_list_access(current_user, filial_id, service.db)
    statuses: list[ServiceOrderStatus] | None
    if view == "active":
        statuses = ACTIVE_STATUSES
    elif view == "history":
        statuses = HISTORY_STATUSES
    else:
        statuses = None
    return await service.list_orders(
        filial_id, statuses, date, technician_user_id, include_drafts=include_drafts
    )


@router.get(
    "/service-orders/page",
    response_model=Page[ServiceOrderPageItem],
    summary="Consultar ODS por páginas",
)
async def page_service_orders(
    filial_id: uuid.UUID = Query(...),
    view: str = Query("active", pattern="^(active|history|all)$"),
    status: ServiceOrderStatus | None = Query(None),
    search: str | None = Query(None, max_length=150),
    date_from: dt.date | None = Query(None),
    date_to: dt.date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    include_drafts: bool = Query(False),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    technician_user_id = await _ensure_list_access(current_user, filial_id, service.db)
    return await service.page_orders(
        filial_id,
        view=view,
        include_drafts=include_drafts,
        status=status,
        search=search,
        date_from=date_from,
        date_to=date_to,
        technician_user_id=technician_user_id,
        page=page,
        page_size=page_size,
    )


@router.get("/service-orders/vehicles", response_model=Page[ServiceVehicleRead])
async def service_vehicles(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(None, max_length=150),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    """Vehículos de la filial para consultar ficha, garantías e historial en Asesor."""
    from sqlalchemy import func, or_, select

    from app.core.pagination import page_query
    from app.core.search import fold_search
    from app.modules.clients.models import Client, Vehicle
    from app.modules.clients.schemas import VehicleRead
    from app.modules.clients.service import ClientService

    await _ensure_access(current_user, filial_id, service.db, AccessLevel.VER)
    query = (
        select(Vehicle, Client.full_name)
        .join(Client, Client.id == Vehicle.client_id)
        .where(Client.filial_id == filial_id)
    )
    if search and search.strip():
        term = search.strip().lower()
        query = query.where(
            or_(
                func.lower(Vehicle.plate).contains(term, autoescape=True),
                func.lower(Vehicle.vin).contains(term, autoescape=True),
                func.lower(Vehicle.brand + " " + Vehicle.model).contains(term, autoescape=True),
                Client.normalized_name.contains(fold_search(term), autoescape=True),
            )
        )
    rows, metadata = await page_query(
        service.db, query.order_by(Vehicle.brand, Vehicle.model, Vehicle.id), page, page_size
    )
    entries = rows.all()
    reader = ClientService(service.db)
    vehicles = [vehicle for vehicle, _ in entries]
    await reader.attach_vehicle_mileage(vehicles)
    await reader.attach_vehicle_maintenance(vehicles)
    return Page[ServiceVehicleRead](
        items=[
            ServiceVehicleRead(vehicle=VehicleRead.model_validate(vehicle), client_name=name)
            for vehicle, name in entries
        ],
        **metadata,
    )


@router.get("/service-orders/{order_id}", response_model=ServiceOrderRead)
async def get_service_order(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> ServiceOrderRead:
    order = await service.get_order(order_id)
    await _ensure_order_access(current_user, order, service.db)
    return order


@router.post(
    "/service-orders", response_model=ServiceOrderRead, status_code=status.HTTP_201_CREATED
)
async def create_service_order(
    payload: ServiceOrderCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> ServiceOrderRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_order(payload, created_by_user_id=current_user.user_id)


@router.patch("/service-orders/{order_id}", response_model=ServiceOrderRead)
async def update_service_order(
    order_id: uuid.UUID,
    payload: ServiceOrderUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> ServiceOrderRead:
    existing = await service.get_order(order_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_order(order_id, payload, current_user)


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
    from app.modules.service_orders.access import has_module_access

    if not await has_module_access(current_user, filial_id, service.db, "ajustes"):
        await _ensure_list_access(current_user, filial_id, service.db)
    return await service.list_bays(filial_id)


@router.post("/bays", response_model=BayRead, status_code=status.HTTP_201_CREATED)
async def create_bay(
    payload: BayCreate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> BayRead:
    await ensure_module_access(service.db, current_user, filial_id, "ajustes", AccessLevel.EDITAR)
    return await service.create_bay(filial_id, payload)


@router.patch("/bays/{bay_id}", response_model=BayRead)
async def update_bay(
    bay_id: uuid.UUID,
    payload: BayUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> BayRead:
    existing = await service.get_bay(bay_id)
    await ensure_module_access(
        service.db, current_user, existing.filial_id, "ajustes", AccessLevel.EDITAR
    )
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
    await _ensure_order_access(current_user, order, service.db)
    return await service.get_order_summary(order_id)


def _task_read(task) -> TaskRead:
    return TaskRead(
        id=task.id,
        tempario_id=task.tempario_id,
        code_snapshot=task.code_snapshot,
        name_snapshot=task.name_snapshot,
        hours_snapshot=float(task.hours_snapshot),
        status=task.status,
        payer=task.payer,
        created_at=task.created_at,
        timer_started_at=task.timer_started_at,
        timer_accumulated_seconds=task.timer_accumulated_seconds,
        notes=task.notes,
        photo_urls=task.photo_urls,
        cancellation_reason=task.cancellation_reason,
        pause_reason=task.pause_reason,
        pause_history=task.pause_history,
    )


@router.get("/service-orders/{order_id}/tasks", response_model=list[TaskRead])
async def list_tasks(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> list[TaskRead]:
    order = await service.get_order(order_id)
    await _ensure_order_access(current_user, order, service.db)
    tasks = await service.list_tasks(order_id)
    return [_task_read(t) for t in tasks]


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
    task = await service.add_task(order_id, payload.tempario_id, payer=payload.payer)
    summary = await service.get_order_summary(order_id)
    summary.warnings = getattr(task, "stock_warnings", [])
    return summary


@router.patch("/service-order-tasks/{task_id}", response_model=TaskRead)
async def update_task_status(
    task_id: uuid.UUID,
    payload: TaskStatusUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> TaskRead:
    order = await service.get_task_order(task_id)
    await _ensure_order_access(current_user, order, service.db, AccessLevel.EDITAR)
    task = await service.update_task_status(task_id, payload.status, payload.reason)
    return _task_read(task)


@router.post("/service-order-tasks/{task_id}/timer/start", response_model=TaskRead)
async def start_task_timer(
    task_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> TaskRead:
    """Manual cronómetro — independent of the task's status. A técnico can
    start it on their own assigned order's tasks, same ownership rule as
    update_task_status above."""
    order = await service.get_task_order(task_id)
    await _ensure_order_access(current_user, order, service.db, AccessLevel.EDITAR)
    task = await service.start_task_timer(task_id)
    return _task_read(task)


@router.post("/service-order-tasks/{task_id}/timer/pause", response_model=TaskRead)
async def pause_task_timer(
    task_id: uuid.UUID,
    payload: TaskPauseInput = TaskPauseInput(),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> TaskRead:
    order = await service.get_task_order(task_id)
    await _ensure_order_access(current_user, order, service.db, AccessLevel.EDITAR)
    task = await service.pause_task_timer(task_id, payload.reason, payload.notes)
    return _task_read(task)


@router.patch("/service-orders/{order_id}/tasks/{task_id}/payer")
async def update_task_payer(
    order_id: uuid.UUID,
    task_id: uuid.UUID,
    payload: TaskPayerUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> OrderSummary:
    filial_id = await service.get_task_filial(task_id)
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    await service.update_task_payer(task_id, payload.payer)
    return await service.get_order_summary(order_id)


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
    returns the refreshed summary. Blocked (see TaskAndTechnicianRequiredError)
    until the order has at least one task and an assigned técnico — checked
    here, not in the service method, so the automatic warranty-claim-to-order
    conversion (which can add a transfer line before a técnico is ever
    assigned) stays unaffected."""
    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    from app.modules.service_orders.guards import require_editable_order

    await require_editable_order(service.db, order_id)
    missing_task = not await service.list_tasks(order_id)
    missing_technician = order.technician_user_id is None
    if missing_task or missing_technician:
        raise TaskAndTechnicianRequiredError(missing_task, missing_technician)
    transfer = await service.add_transfer_line(
        order_id, payload.part_id, payload.quantity, payer=payload.payer
    )
    summary = await service.get_order_summary(order_id)
    summary.warnings = getattr(transfer, "stock_warnings", [])
    return summary


@router.patch("/service-orders/{order_id}/transfers/lines/{line_id}/payer")
async def update_transfer_line_payer(
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    payload: TransferLinePayerUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> OrderSummary:
    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    await service.update_transfer_line_payer(line_id, payload.payer)
    return await service.get_order_summary(order_id)


@router.patch("/service-orders/{order_id}/transfers/lines/{line_id}/quantity")
async def update_transfer_line_quantity(
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    payload: TransferLineQuantityUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> OrderSummary:
    """Changes a line's requested quantity — only while its ODT is still
    Pendiente (see TransferLineNotEditableError)."""
    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    transfer = await service.set_transfer_line_quantity(line_id, payload.quantity)
    summary = await service.get_order_summary(order_id)
    summary.warnings = getattr(transfer, "stock_warnings", [])
    return summary


@router.delete("/service-orders/{order_id}/transfers/lines/{line_id}")
async def remove_transfer_line(
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> OrderSummary:
    """Drops a line entirely — only while its ODT is still Pendiente."""
    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    await service.remove_transfer_line(line_id)
    return await service.get_order_summary(order_id)


@router.delete("/service-order-transfers/{transfer_id}", status_code=204)
async def delete_transfer(
    transfer_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> None:
    """Elimina únicamente una ODT pendiente; rechaza pedidas y completadas."""
    from app.modules.service_orders.dispatch import WorkshopDispatchService

    filial_id = await service.get_transfer_filial(transfer_id)
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    await WorkshopDispatchService(service.db).delete(transfer_id)


@router.delete("/service-orders/{order_id}/transfers/{transfer_id}", status_code=204)
async def delete_order_transfer(
    order_id: uuid.UUID,
    transfer_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> None:
    """Elimina una ODT perteneciente a esta ODS; rechaza IDs de otra orden."""
    from app.modules.service_orders.dispatch import WorkshopDispatchService

    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    await WorkshopDispatchService(service.db).delete(transfer_id, order_id=order_id)


@router.post("/service-order-transfers/{transfer_id}/mark-ordered", response_model=TransferRead)
async def mark_transfer_ordered(
    transfer_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> TransferRead:
    """Submit the request and reserve stock without consuming inventory."""
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
                "subtotal": float(line.line_total),
                "payer": line.payer,
            }
            for line in transfer.lines
        ],
        subtotal=sum(float(line.line_total) for line in transfer.lines),
        fulfilled_by_user_id=transfer.fulfilled_by_user_id,
        fulfilled_at=transfer.fulfilled_at,
        completed_by_user_id=transfer.completed_by_user_id,
        completed_at=transfer.completed_at,
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
    return await service.list_upsells(filial_id)


@router.post(
    "/service-orders/{order_id}/upsells",
    response_model=UpsellRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_upsell(
    order_id: uuid.UUID,
    title: str = Form(...),
    description: str = Form(...),
    severity: UpsellSeverity = Form(...),
    detected_by_user_id: uuid.UUID | None = Form(default=None),
    # JSON-encoded lists — Form doesn't support nested list-of-object
    # fields, so these arrive as strings and get validated below.
    tasks_json: str = Form(default="[]"),
    parts_json: str = Form(default="[]"),
    photos: list[UploadFile] = File(default=[]),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> UpsellRead:
    order = await service.get_order(order_id)
    from app.modules.auth.exceptions import InsufficientPermissionsError
    from app.modules.service_orders.guards import require_editable_order

    await _ensure_order_access(current_user, order, service.db, AccessLevel.EDITAR)
    order = await require_editable_order(service.db, order_id)

    await ensure_module_access(
        service.db, current_user, order.filial_id, "tecnico-servicio", AccessLevel.EDITAR
    )
    if order.is_draft or order.technician_user_id != current_user.user_id:
        raise InsufficientPermissionsError()

    payload = _validated_form(
        UpsellCreate,
        title=title,
        description=description,
        severity=severity,
        detected_by_user_id=current_user.user_id,
        tasks=_form_items(UpsellTaskInput, tasks_json, "tasks_json"),
        parts=_form_items(UpsellPartInput, parts_json, "parts_json"),
    )

    settings = get_settings()
    photo_urls = [
        await save_upload_image(
            photo,
            directory=Path(settings.uploads_dir),
            subdir="upsells",
            url_prefix=f"{settings.api_v1_prefix}/uploads",
            max_mb=settings.max_upload_mb,
        )
        for photo in photos
    ]

    return await service.create_upsell(order_id, payload, photo_urls)


@router.patch("/upsells/{upsell_id}", response_model=UpsellRead)
async def decide_upsell(
    upsell_id: uuid.UUID,
    payload: UpsellDecisionInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> UpsellRead:
    existing = await service.get_upsell(upsell_id)
    order = await service.get_order(existing.service_order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    return await service.decide_upsell(upsell_id, payload, current_user.user_id)


@router.get("/upsells/pending-by-vehicle", response_model=list[PendingUpsellRead])
async def list_pending_upsells_for_vehicle(
    vehicle_id: uuid.UUID = Query(...),
    exclude_order_id: uuid.UUID | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> list[PendingUpsellRead]:
    """Recomendaciones pendientes/pospuestas de este vehículo, de OTRAS
    visitas — el bloque fijo que un asesor ve al abrir la ODS de este VIN."""
    filial_id = await service.get_vehicle_filial_id(vehicle_id)
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_pending_upsells_for_vehicle(vehicle_id, exclude_order_id)


@router.get("/service-orders/{order_id}/billing")
async def billing_context(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.billing import BillingService

    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db)
    return await BillingService(service.db).context(order_id)


@router.post("/service-orders/{order_id}/billing/quote")
async def billing_quote(
    order_id: uuid.UUID,
    payload: BillingInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.billing import BillingService

    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db)
    return await BillingService(service.db).quote(order_id, payload)


@router.post("/service-orders/{order_id}/billing/refresh-rate")
async def refresh_billing_rate(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    import subprocess

    from app.core.exceptions import BadRequestError
    from app.modules.exchange_rates.service import ExchangeRateService
    from app.modules.service_orders.billing import BillingService

    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    from app.modules.service_orders.guards import require_editable_order

    await require_editable_order(service.db, order_id)
    try:
        await ExchangeRateService(service.db).refresh()
    except (ValueError, subprocess.SubprocessError, OSError) as exc:
        raise BadRequestError(
            "No se pudo actualizar la tasa desde BCV. Intenta nuevamente."
        ) from exc
    return await BillingService(service.db).context(order_id)


@router.post("/service-orders/{order_id}/invoice", status_code=status.HTTP_201_CREATED)
async def issue_invoice(
    order_id: uuid.UUID,
    payload: InvoiceCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.billing import BillingService

    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    invoice = await BillingService(service.db).issue(order_id, payload, current_user.user_id)
    return invoice.document


@router.get("/service-orders/{order_id}/invoice")
async def read_invoice(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.billing import BillingService

    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db)
    return (await BillingService(service.db).get_invoice(order_id)).document


@router.get("/service-orders/{order_id}/invoice/document")
async def invoice_document(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.billing import BillingService
    from app.modules.service_orders.invoice_document import render_invoice

    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db)
    invoice = await BillingService(service.db).get_invoice(order_id)
    return {"filename": f"{invoice.code}.html", "html": render_invoice(invoice.document)}


@router.get("/service-orders/{order_id}/invoice/pdf")
async def invoice_pdf(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from fastapi.responses import Response

    from app.core.documents.invoice_pdf import from_service_invoice, render_invoice_pdf
    from app.modules.service_orders.billing import BillingService

    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db)
    invoice = await BillingService(service.db).get_invoice(order_id)
    # UUID filename avoids trusting editable document codes in response headers.
    return Response(
        render_invoice_pdf(from_service_invoice(invoice.document)),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="factura-{invoice.id}.pdf"'},
    )


# Cuentas por cobrar — top-level paths (not nested under /service-orders/{order_id})
# since a receivable belongs to an invoice, not to a single order lookup.


@router.get("/receivables", response_model=list[ReceivableRead])
async def list_receivables(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> list[ReceivableRead]:
    from app.modules.administracion.service import AdministracionService

    await _ensure_access(current_user, filial_id, service.db)
    return await AdministracionService(service.db).list_receivables(filial_id)


@router.post("/receivables/{invoice_id}/collect", response_model=ReceivableRead)
async def collect_receivable(
    invoice_id: uuid.UUID,
    payload: CollectInvoicePaymentInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> ReceivableRead:
    from app.modules.service_orders.billing import BillingService

    billing = BillingService(service.db)
    invoice = await billing.get_invoice_by_id(invoice_id)
    order = await service.get_order(invoice.service_order_id)
    await _ensure_cobrar_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    return await billing.collect_invoice(invoice_id, payload, current_user.user_id)


@router.post("/service-orders/{order_id}/close", response_model=ServiceOrderRead)
async def close_service_order(
    order_id: uuid.UUID,
    payload: ServiceOrderCloseInput = ServiceOrderCloseInput(),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    return await service.close_order(
        order_id,
        payload.next_maintenance_due_at,
        payload.next_maintenance_tempario_id,
        exit_mileage=payload.exit_mileage,
    )


@router.post("/service-orders/{order_id}/force-complete", response_model=ServiceOrderRead)
async def force_complete_service_order(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> ServiceOrderRead:
    """Manual override — normally the order completes on its own once every
    task is done and every ODT dispatched (see ServiceOrderService._sync_status_from_tasks).
    This forces it anyway with something still pending."""
    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    return await service.force_complete_order(order_id, current_user)


@router.post("/service-orders/{order_id}/cancel", response_model=ServiceOrderRead)
async def cancel_service_order(
    order_id: uuid.UUID,
    payload: ServiceOrderCancelInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> ServiceOrderRead:
    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    return await service.cancel_order(order_id, payload.reason, current_user.user_id)


@router.post("/service-orders/{order_id}/reopen", response_model=ServiceOrderRead)
async def reopen_service_order(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> ServiceOrderRead:
    order = await service.get_order(order_id)
    # Reopening reverses a decision already made on the floor — same
    # elevated bar as authorizing/rejecting a warranty claim, not a routine
    # asesor-level edit.
    await ensure_module_access(
        service.db, current_user, order.filial_id, "administracion", AccessLevel.EDITAR
    )
    return await service.reopen_order(order_id, current_user.user_id)


# Warranty claims (unified "reclamo de garantía") — covers factory,
# comeback (garantía de taller), defective-part (proveedor), and
# campaign/recall claims in one flow: solicitado -> autorizado/rechazado ->
# (if autorizado) convertido_a_ods.


@router.get("/warranty-claims/context", response_model=WarrantyClaimContext)
async def get_warranty_claim_context(
    vehicle_id: uuid.UUID = Query(...),
    service_order_id: uuid.UUID | None = Query(default=None),
    tempario_id: uuid.UUID | None = Query(default=None),
    part_id: uuid.UUID | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> WarrantyClaimContext:
    filial_id = await service.get_vehicle_filial_id(vehicle_id)
    await _ensure_access(current_user, filial_id, service.db)
    return await service.get_claim_context(vehicle_id, service_order_id, tempario_id, part_id)


@router.get("/warranty-claims", response_model=list[WarrantyClaimRead])
async def list_warranty_claims(
    filial_id: uuid.UUID = Query(...),
    status_filter: WarrantyClaimStatus | None = Query(default=None, alias="status"),
    vehicle_id: uuid.UUID | None = Query(default=None),
    service_order_id: uuid.UUID | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> list[WarrantyClaimRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_warranty_claims(
        filial_id, status_filter, vehicle_id, service_order_id
    )


async def _ensure_claim_manager(user, filial_id, db):
    # Administracion is the permission granted to jefe de taller / gerente.
    await ensure_module_access(db, user, filial_id, "administracion", AccessLevel.EDITAR)


@router.get("/warranty-claims/overview")
async def claims_overview(
    filial_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.claims_workflow import ClaimsWorkflow

    await _ensure_access(current_user, filial_id, service.db)
    return await ClaimsWorkflow(service.db).overview(filial_id)


@router.post("/warranty-claims/from-warranty", response_model=WarrantyClaimRead, status_code=201)
async def claim_from_warranty(
    vehicle_id: uuid.UUID = Form(...),
    reported_mileage: int = Form(...),
    symptom: str = Form(...),
    workshop_warranty_id: uuid.UUID | None = Form(None),
    factory_warranty_id: uuid.UUID | None = Form(None),
    campaign: bool = Form(False),
    photos: list[UploadFile] = File(default=[]),
    documents: list[UploadFile] = File(default=[]),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.claims_workflow import ClaimsWorkflow
    from app.modules.service_orders.schemas import WarrantyClaimFromWarranty

    filial_id = await service.get_vehicle_filial_id(vehicle_id)
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    payload = _validated_form(
        WarrantyClaimFromWarranty,
        vehicle_id=vehicle_id,
        reported_mileage=reported_mileage,
        symptom=symptom,
        workshop_warranty_id=workshop_warranty_id,
        factory_warranty_id=factory_warranty_id,
        campaign=campaign,
    )
    settings = get_settings()
    saved = []
    for files in [photos, documents]:
        saved.append(
            [
                await save_upload_attachment(
                    file,
                    directory=Path(settings.uploads_dir),
                    subdir="warranty-claims",
                    url_prefix=f"{settings.api_v1_prefix}/uploads",
                    max_mb=settings.max_upload_mb,
                )
                for file in files
            ]
        )
    return await ClaimsWorkflow(service.db).create(payload, saved[0], saved[1], current_user)


@router.get("/warranty-claims/{claim_id}", response_model=WarrantyClaimRead)
async def get_warranty_claim(
    claim_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> WarrantyClaimRead:
    claim = await service.get_warranty_claim(claim_id)
    filial_id = await service.get_vehicle_filial_id(claim.vehicle_id)
    await _ensure_access(current_user, filial_id, service.db)
    return claim


@router.post(
    "/warranty-claims", response_model=WarrantyClaimRead, status_code=status.HTTP_201_CREATED
)
async def create_warranty_claim(
    claim_type: WarrantyClaimType = Form(...),
    vehicle_id: uuid.UUID = Form(...),
    service_order_id: uuid.UUID | None = Form(default=None),
    tempario_id: uuid.UUID | None = Form(default=None),
    part_id: uuid.UUID | None = Form(default=None),
    failure_category: ReworkFailureCategory | None = Form(default=None),
    failure_cause: str | None = Form(default=None),
    reported_symptom: str | None = Form(default=None),
    reported_mileage: int = Form(...),
    claimed_at: dt.date = Form(default_factory=dt.date.today),
    note: str | None = Form(default=None),
    photos: list[UploadFile] = File(default=[]),
    documents: list[UploadFile] = File(default=[]),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> WarrantyClaimRead:
    filial_id = await service.get_vehicle_filial_id(vehicle_id)
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)

    settings = get_settings()
    photo_urls = [
        await save_upload_attachment(
            photo,
            directory=Path(settings.uploads_dir),
            subdir="warranty-claims",
            url_prefix=f"{settings.api_v1_prefix}/uploads",
            max_mb=settings.max_upload_mb,
        )
        for photo in photos
    ]
    document_urls = [
        await save_upload_attachment(
            document,
            directory=Path(settings.uploads_dir),
            subdir="warranty-claims",
            url_prefix=f"{settings.api_v1_prefix}/uploads",
            max_mb=settings.max_upload_mb,
        )
        for document in documents
    ]

    payload = WarrantyClaimCreate(
        claim_type=claim_type,
        vehicle_id=vehicle_id,
        service_order_id=service_order_id,
        tempario_id=tempario_id,
        part_id=part_id,
        failure_category=failure_category,
        failure_cause=failure_cause,
        reported_symptom=reported_symptom,
        reported_mileage=reported_mileage,
        claimed_at=claimed_at,
        note=note,
    )
    return await service.create_warranty_claim(
        payload, photo_urls, document_urls, current_user.user_id
    )


@router.post("/warranty-claims/{claim_id}/authorize", response_model=WarrantyClaimRead)
async def authorize_warranty_claim(
    claim_id: uuid.UUID,
    payload: WarrantyClaimAuthorizationInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> WarrantyClaimRead:
    # Deliberately gated by the "administracion" module (jefe de
    # taller/gerente territory — reclamos are already its stated job, see
    # seed_roles.py), NOT "asesor-servicios" — an asesor can create a claim,
    # but cannot approve or reject its own warranty request.
    claim = await service.get_warranty_claim(claim_id)
    filial_id = await service.get_vehicle_filial_id(claim.vehicle_id)
    await ensure_module_access(
        service.db, current_user, filial_id, "administracion", AccessLevel.EDITAR
    )
    return await service.authorize_warranty_claim(claim_id, payload, current_user.user_id)


@router.post("/warranty-claims/{claim_id}/convert-to-order", response_model=WarrantyClaimRead)
async def convert_warranty_claim_to_order(
    claim_id: uuid.UUID,
    payload: WarrantyClaimConvertInput = WarrantyClaimConvertInput(),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> WarrantyClaimRead:
    # Same gate as authorize — converting an authorized claim into a real
    # order is the continuation of the same approval decision.
    claim = await service.get_warranty_claim(claim_id)
    filial_id = await service.get_vehicle_filial_id(claim.vehicle_id)
    await ensure_module_access(
        service.db, current_user, filial_id, "administracion", AccessLevel.EDITAR
    )
    return await service.convert_warranty_claim_to_order(claim_id, payload, current_user.user_id)


@router.get("/service-order-transfers/{transfer_id}/request-preview")
async def preview_workshop_request(
    transfer_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.dispatch import WorkshopDispatchService

    filial_id = await service.get_transfer_filial(transfer_id)
    await _ensure_access(current_user, filial_id, service.db)
    _, _, preview = await WorkshopDispatchService(service.db).preview(transfer_id)
    return preview


@router.post(
    "/service-order-transfers/{transfer_id}/pickup", status_code=status.HTTP_204_NO_CONTENT
)
async def confirm_workshop_pickup(
    transfer_id: uuid.UUID,
    photo: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.dispatch import WorkshopDispatchService

    dispatch = WorkshopDispatchService(service.db)
    request = await dispatch.request(transfer_id, lock=True)
    order = await service.get_order(request.service_order_id)
    await _ensure_order_access(current_user, order, service.db, AccessLevel.EDITAR)
    if request.status.value != "completado":
        from app.core.exceptions import BadRequestError

        raise BadRequestError("El almacén debe despachar antes de confirmar el retiro.")
    if request.picked_up_at is None:
        from app.core.storage import save_private_pickup

        request.pickup_photo_url = await save_private_pickup(photo)
        request.receipt_incident = None
        request.picked_up_at = dt.datetime.now(dt.UTC)
        request.picked_up_by_user_id = current_user.user_id
        await service.db.flush()
        await service._sync_status_from_tasks(order)
        await service.db.commit()


@router.post("/service-orders/{order_id}/vehicle-received", response_model=ServiceOrderRead)
async def receive_vehicle(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.guards import require_editable_order

    order = await require_editable_order(service.db, order_id)
    await _ensure_order_access(current_user, order, service.db, AccessLevel.EDITAR)
    if order.vehicle_received_at is None:
        order.vehicle_received_at = dt.datetime.now(dt.UTC)
        order.vehicle_received_by_user_id = current_user.user_id
        await service.db.commit()
        await service.db.refresh(order)
    return order


@router.patch("/service-order-tasks/{task_id}/notes", response_model=TaskRead)
async def task_notes(
    task_id: uuid.UUID,
    payload: TaskNotesInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.guards import require_editable_order
    from app.modules.service_orders.models import ServiceOrderTask

    order = await service.get_task_order(task_id)
    await _ensure_order_access(current_user, order, service.db, AccessLevel.EDITAR)
    await require_editable_order(service.db, order.id)
    task = await service.db.get(ServiceOrderTask, task_id)
    task.notes = payload.notes
    await service.db.commit()
    return _task_read(task)


@router.post("/service-order-tasks/{task_id}/photos", response_model=TaskRead)
async def task_photos(
    task_id: uuid.UUID,
    photos: list[UploadFile] = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.core.exceptions import BadRequestError
    from app.modules.service_orders.guards import require_editable_order
    from app.modules.service_orders.models import ServiceOrderTask

    order = await service.get_task_order(task_id)
    await _ensure_order_access(current_user, order, service.db, AccessLevel.EDITAR)
    await require_editable_order(service.db, order.id)
    task = await service.db.get(ServiceOrderTask, task_id)
    if len(task.photo_urls or []) + len(photos) > 10:
        raise BadRequestError("Máximo 10 fotos por tarea.")
    urls = list(task.photo_urls or [])
    for photo in photos:
        urls.append(
            await save_upload_image(
                photo,
                directory=Path(get_settings().uploads_dir),
                subdir="task-evidence",
                url_prefix=f"{get_settings().api_v1_prefix}/uploads",
                max_mb=get_settings().max_upload_mb,
            )
        )
    task.photo_urls = urls
    await service.db.commit()
    return _task_read(task)


@router.post("/service-order-transfers/{transfer_id}/not-received", response_model=TransferRead)
async def transfer_not_received(
    transfer_id: uuid.UUID,
    payload: TransferNotReceivedInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.core.exceptions import ConflictError
    from app.modules.service_orders.dispatch import WorkshopDispatchService
    from app.modules.service_orders.guards import require_editable_order

    transfer = await WorkshopDispatchService(service.db).request(transfer_id, lock=True)
    order = await require_editable_order(service.db, transfer.service_order_id)
    await _ensure_order_access(current_user, order, service.db, AccessLevel.EDITAR)
    if transfer.status.value != "completado" or transfer.picked_up_at:
        raise ConflictError(
            "Solo puedes reportar un despacho que todavía no has recibido.",
            error_code="receipt_incident_not_allowed",
        )
    incident = {
        "reason": payload.reason,
        "notes": payload.notes,
        "reported_at": dt.datetime.now(dt.UTC).isoformat(),
        "reported_by_user_id": str(current_user.user_id),
    }
    transfer.receipt_incident = incident
    transfer.receipt_incident_history = [*(transfer.receipt_incident_history or []), incident]
    await service.db.commit()
    summary = await service.get_order_summary(order.id)
    return next(t for t in summary.transfers if t.id == transfer_id)


@router.get("/service-order-transfers/{transfer_id}/pickup-photo")
async def pickup_photo(
    transfer_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    import asyncio

    from fastapi.responses import FileResponse, RedirectResponse

    from app.core.exceptions import NotFoundError
    from app.core.storage import _s3_client, private_pickup_location
    from app.modules.service_orders.dispatch import WorkshopDispatchService

    transfer = await WorkshopDispatchService(service.db).request(transfer_id)
    order = await service.get_order(transfer.service_order_id)
    # Almacén and this order's technician/advisor can inspect the receipt.
    from app.modules.service_orders.access import has_module_access

    if not await has_module_access(current_user, order.filial_id, service.db, "almacen"):
        await _ensure_order_access(current_user, order, service.db)
    if not transfer.pickup_photo_url:
        raise NotFoundError("Esta ODT no tiene foto de retiro.")
    kind, bucket, location = private_pickup_location(transfer.pickup_photo_url)
    if kind == "s3":
        url = await asyncio.to_thread(
            _s3_client().generate_presigned_url,
            "get_object",
            Params={"Bucket": bucket, "Key": location},
            ExpiresIn=300,
        )
        return RedirectResponse(url, headers={"Cache-Control": "no-store"})
    if not Path(location).is_file():
        raise NotFoundError("Archivo no disponible.")
    return FileResponse(location, headers={"Cache-Control": "no-store"})


@router.get("/upsells/{upsell_id}/quote")
async def quote_upsell(
    upsell_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    """Current reception estimate, separate from the immutable original amount."""
    from decimal import Decimal

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.core.exceptions import ConflictError
    from app.modules.parts.models import Part
    from app.modules.parts.pricing import DEFAULT_DISCOUNT, PARTS_MULTIPLIERS
    from app.modules.post_ventas.models import Tempario
    from app.modules.post_ventas.service import PostVentasService

    finding = await service._get_upsell_model(upsell_id)
    order = await service.get_order(finding.service_order_id)
    await _ensure_access(current_user, order.filial_id, service.db)
    settings = await PostVentasService(service.db).get_labor_settings(order.filial_id, commit=False)
    labor = Decimal(0)
    quantities = {}
    for task in finding.tasks:
        tempario = (
            await service.db.execute(
                select(Tempario)
                .where(Tempario.id == task.tempario_id)
                .options(selectinload(Tempario.parts))
            )
        ).scalar_one_or_none()
        if tempario is None or not tempario.is_active:
            raise ConflictError(
                "Un servicio de la recomendación ya no está disponible.",
                error_code="upsell_catalog_unavailable",
            )
        labor += Decimal(str(tempario.estimated_hours)) * Decimal(str(settings.hourly_rate))
        for part in tempario.parts:
            if part.part_id:
                quantities[part.part_id] = quantities.get(part.part_id, 0) + part.quantity
    for part in finding.parts:
        quantities[part.part_id] = quantities.get(part.part_id, 0) + part.quantity
    costs = Decimal(0)
    for part_id, quantity in quantities.items():
        part = await service.db.get(Part, part_id)
        if part is None:
            raise ConflictError(
                "Un repuesto ya no está disponible.", error_code="upsell_catalog_unavailable"
            )
        # Same FIFO preview used by creating a recommendation; estimate is not a reservation.
        total_cost, _allocations, _shortfall = await service._fifo_preview_cost(
            order.filial_id, part_id, quantity
        )
        costs += total_cost
    parts = costs * Decimal(str(PARTS_MULTIPLIERS[DEFAULT_DISCOUNT]))
    subtotal = labor + parts
    iva = subtotal * Decimal(str(settings.iva_percentage)) / 100
    return {
        "upsell_id": finding.id,
        "original_amount": finding.quoted_price_snapshot,
        "has_work": bool(finding.tasks or finding.parts),
        "labor": round(labor, 2),
        "parts": round(parts, 2),
        "subtotal": round(subtotal, 2),
        "iva": round(iva, 2),
        "total": round(subtotal + iva, 2),
        "estimated_at": dt.datetime.now(dt.UTC),
        "is_estimate": True,
    }


@router.post("/service-order-drafts", response_model=ServiceOrderRead, status_code=201)
async def create_order_draft(
    payload: ServiceOrderDraftCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.drafts import DraftService

    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await DraftService(service.db).create(payload, current_user)


@router.patch("/service-order-drafts/{order_id}", response_model=ServiceOrderRead)
async def update_order_draft(
    order_id: uuid.UUID,
    payload: ServiceOrderDraftUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.drafts import DraftService

    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    return await DraftService(service.db).update(order_id, payload)


@router.post("/service-order-drafts/{order_id}/submit", response_model=ServiceOrderRead)
async def submit_order_draft(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.drafts import DraftService

    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    return await DraftService(service.db).submit(order_id, current_user)


@router.delete("/service-order-drafts/{order_id}", status_code=204)
async def discard_order_draft(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.drafts import DraftService

    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    await DraftService(service.db).discard(order_id)


@router.get("/service-orders/{order_id}/inspection-records")
async def order_inspection_records(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.detailed_inspections.service import DetailedInspectionService
    from app.modules.inspections.schemas import InspectionRead
    from app.modules.inspections.service import InspectionService

    order = await service.get_order(order_id)
    await _ensure_order_access(current_user, order, service.db)
    preliminary = await InspectionService(service.db).get_for_order(order_id)
    detailed_service = DetailedInspectionService(service.db)
    detailed = await detailed_service.read(await detailed_service.get(order_id), order)
    return {
        "service_order_id": order.id,
        "preliminary": InspectionRead.model_validate(preliminary) if preliminary else None,
        "detailed": detailed,
    }


@router.post("/warranty-claims/{claim_id}/review", response_model=WarrantyClaimRead)
async def review_claim(
    claim_id: uuid.UUID,
    payload: ClaimReviewInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.claims_workflow import ClaimsWorkflow

    claim = await service.get_warranty_claim(claim_id)
    await _ensure_claim_manager(current_user, claim.filial_id, service.db)
    return await ClaimsWorkflow(service.db).review(claim_id, payload, current_user)


@router.post("/warranty-claims/{claim_id}/brand-response", response_model=WarrantyClaimRead)
async def record_brand_response(
    claim_id: uuid.UUID,
    payload: ClaimReviewInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from app.modules.service_orders.claims_workflow import ClaimsWorkflow

    claim = await service.get_warranty_claim(claim_id)
    await _ensure_claim_manager(current_user, claim.filial_id, service.db)
    return await ClaimsWorkflow(service.db).review(claim_id, payload, current_user, brand=True)


@router.get("/warranty-claims/{claim_id}/installed-parts")
async def claim_installed_parts(
    claim_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from sqlalchemy import select

    from app.modules.parts.models import Part
    from app.modules.service_orders.models import ServiceOrderTransfer, ServiceOrderTransferLine

    claim = await service.get_warranty_claim(claim_id)
    await _ensure_access(current_user, claim.filial_id, service.db)
    rows = (
        await service.db.execute(
            select(Part.id, Part.name, Part.code)
            .join(ServiceOrderTransferLine, ServiceOrderTransferLine.part_id == Part.id)
            .join(
                ServiceOrderTransfer,
                ServiceOrderTransfer.id == ServiceOrderTransferLine.transfer_id,
            )
            .where(
                ServiceOrderTransfer.service_order_id == claim.service_order_id,
                ServiceOrderTransfer.stock_deducted.is_(True),
            )
            .distinct()
        )
    ).all()
    return [{"id": str(row.id), "name": row.name, "code": row.code} for row in rows]


@router.post("/warranty-claims/{claim_id}/supplier-claim", response_model=WarrantyClaimRead)
async def create_claim_to_supplier(
    claim_id: uuid.UUID,
    part_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
):
    from sqlalchemy import select

    from app.core.exceptions import ConflictError
    from app.modules.service_orders.models import WarrantyClaim

    claim = (
        await service.db.execute(
            select(WarrantyClaim).where(WarrantyClaim.id == claim_id).with_for_update()
        )
    ).scalar_one_or_none()
    if claim is None:
        return await service.get_warranty_claim(claim_id)
    await _ensure_claim_manager(current_user, claim.filial_id, service.db)
    if (
        claim.claim_type != WarrantyClaimType.COMEBACK
        or claim.status != WarrantyClaimStatus.CONVERTIDO_A_ODS
    ):
        raise ConflictError("Solo se puede reclamar al proveedor desde un comeback autorizado.")
    original = await service.get_order(claim.service_order_id)
    await service._auto_claim_defective_part(original, claim, part_id=part_id)
    from app.modules.administracion.models import SupplierClaim

    found = (
        await service.db.execute(
            select(SupplierClaim.id)
            .where(SupplierClaim.warranty_claim_id == claim.id, SupplierClaim.part_id == part_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if found is None:
        raise ConflictError(
            "No hay un lote de compra despachado para este repuesto en la ODS de origen."
        )
    claim.part_id = part_id
    await service.db.commit()
    return await service.get_warranty_claim(claim_id)
