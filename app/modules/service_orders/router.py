import datetime as dt
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.storage import save_upload_attachment
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access
from app.modules.service_orders.billing_schemas import (
    BillingInput,
    CollectInvoicePaymentInput,
    InvoiceCreate,
    ReceivableRead,
)
from app.modules.service_orders.enums import ReworkFailureCategory, ServiceOrderStatus, WarrantyClaimStatus, WarrantyClaimType
from app.modules.service_orders.exceptions import TaskAndTechnicianRequiredError
from app.modules.service_orders.schemas import (
    BayCreate,
    BayRead,
    BayUpdate,
    OrderSummary,
    ServiceOrderCancelInput,
    ServiceOrderCloseInput,
    ServiceOrderCreate,
    ServiceOrderRead,
    ServiceOrderUpdate,
    TaskCreate,
    TaskPayerUpdate,
    TaskRead,
    TaskStatusUpdate,
    TransferLineInput,
    TransferLinePayerUpdate,
    TransferLineQuantityUpdate,
    TransferRead,
    UpsellCreate,
    UpsellDecisionInput,
    UpsellRead,
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


@router.post(
    "/service-orders", response_model=ServiceOrderRead, status_code=status.HTTP_201_CREATED
)
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
            payer=t.payer,
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
        payer=task.payer,
        created_at=task.created_at,
    )


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
    transfer = await service.add_transfer_line(order_id, payload.part_id, payload.quantity, payer=payload.payer)
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
    payload: UpsellCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ServiceOrderService = Depends(get_service),
) -> UpsellRead:
    order = await service.get_order(order_id)
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_upsell(order_id, payload)


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
    await _ensure_access(current_user, order.filial_id, service.db, AccessLevel.EDITAR)
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
        order_id, payload.next_maintenance_due_at, payload.next_maintenance_tempario_id
    )


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
    return await service.list_warranty_claims(filial_id, status_filter, vehicle_id, service_order_id)


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


@router.post("/warranty-claims", response_model=WarrantyClaimRead, status_code=status.HTTP_201_CREATED)
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
    return await service.create_warranty_claim(payload, photo_urls, document_urls, current_user.user_id)


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
    await ensure_module_access(service.db, current_user, filial_id, "administracion", AccessLevel.EDITAR)
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
    await ensure_module_access(service.db, current_user, filial_id, "administracion", AccessLevel.EDITAR)
    return await service.convert_warranty_claim_to_order(claim_id, payload, current_user.user_id)
