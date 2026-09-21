import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.parts.pricing import DEFAULT_DISCOUNT, DiscountLabel
from app.modules.post_ventas.schemas import VehicleWarrantyRead, WorkshopWarrantyRead
from app.modules.service_orders.enums import (
    ReworkFailureCategory,
    ServiceOrderPayer,
    ServiceOrderStatus,
    ServiceOrderType,
    TaskStatus,
    TransferStatus,
    UpsellApprovalChannel,
    UpsellStatus,
    WarrantyClaimStatus,
    WarrantyClaimType,
)


class BayCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class BayUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    is_active: bool | None = None


class BayRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    name: str
    is_active: bool


class ServiceOrderCreate(BaseModel):
    discount_label: DiscountLabel = DEFAULT_DISCOUNT
    filial_id: uuid.UUID
    vehicle_id: uuid.UUID
    order_type: ServiceOrderType = ServiceOrderType.REGULAR
    # Reception data. customer_reason can be inherited from an unlinked
    # PreliminaryInspection for this vehicle; advisor_user_id/promised_at
    # have no inspection equivalent and are always entered by hand.
    # intake_mileage is never entered by hand — it's always a read-only
    # view inherited from a PreliminaryInspection, set server-side (see
    # ServiceOrderService.create_order). A walk-in ODS (no scheduled_at)
    # must reference an existing unlinked inspection for this vehicle here;
    # one scheduled ahead via "Agendar Orden de Servicio" can't know it yet
    # (the vehicle isn't there), so inspection_id may be omitted and gets
    # linked later, from the order detail screen, once it arrives.
    inspection_id: uuid.UUID | None = None
    customer_reason: str = Field(min_length=1, max_length=2000)
    advisor_user_id: uuid.UUID
    promised_at: date
    notes: str | None = None
    # Used by "Agendar Orden de Servicio" in the Calendario view — optional
    # so a normal walk-in ODS (created from the kanban) can omit them.
    scheduled_at: datetime | None = None
    technician_user_id: uuid.UUID | None = None
    bay_id: uuid.UUID | None = None


class ServiceOrderUpdate(BaseModel):
    discount_label: DiscountLabel | None = None
    status: ServiceOrderStatus | None = None
    order_type: ServiceOrderType | None = None
    technician_user_id: uuid.UUID | None = None
    advisor_user_id: uuid.UUID | None = None
    bay_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None
    notes: str | None = None
    # "None" above means "leave unchanged" — these flags are how the client
    # explicitly asks to clear a nullable assignment back to "Sin asignar".
    clear_technician: bool = False
    clear_advisor: bool = False
    clear_bay: bool = False
    # Only meaningful when transitioning status to "completado" while a task
    # is still pendiente or an ODT hasn't been marked pedido — the server
    # decides whether that's actually the case, this just carries the
    # explicit confirmation to go ahead anyway.
    confirm_incomplete_completion: bool = False


class ServiceOrderCancelInput(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class ServiceOrderCloseInput(BaseModel):
    # Optional "next visit suggested" date for the vehicle, entered by the
    # advisor at close time — feeds the "mantenimiento por vencer" list.
    next_maintenance_due_at: date | None = None
    # Optional plan task (Tempario) the advisor flags as pending for the
    # vehicle's next visit — lets the next ODS offer to load it in one click.
    next_maintenance_tempario_id: uuid.UUID | None = None


class ServiceOrderRead(BaseModel):
    discount_label: DiscountLabel
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    code: str
    vehicle_id: uuid.UUID
    status: ServiceOrderStatus
    order_type: ServiceOrderType
    technician_user_id: uuid.UUID | None
    advisor_user_id: uuid.UUID | None
    bay_id: uuid.UUID | None
    notes: str | None
    scheduled_at: datetime | None
    intake_mileage: int | None
    customer_reason: str | None
    promised_at: date | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    total_amount: float | None
    invoiced_at: datetime | None
    cancel_reason: str | None
    cancelled_by_user_id: uuid.UUID | None
    cancelled_at: datetime | None
    reopened_by_user_id: uuid.UUID | None
    reopened_at: datetime | None
    completed_with_pending_items: bool
    completed_override_by_user_id: uuid.UUID | None
    completed_override_at: datetime | None


class TaskCreate(BaseModel):
    tempario_id: uuid.UUID
    payer: ServiceOrderPayer = ServiceOrderPayer.CLIENTE


class TaskStatusUpdate(BaseModel):
    status: TaskStatus


class TaskPayerUpdate(BaseModel):
    payer: ServiceOrderPayer


class TaskRead(BaseModel):
    id: uuid.UUID
    tempario_id: uuid.UUID
    code_snapshot: str
    name_snapshot: str
    hours_snapshot: float
    status: TaskStatus
    payer: ServiceOrderPayer
    created_at: datetime


class TransferLineInput(BaseModel):
    part_id: uuid.UUID
    quantity: int = Field(ge=1)
    payer: ServiceOrderPayer = ServiceOrderPayer.CLIENTE


class TransferLinePayerUpdate(BaseModel):
    payer: ServiceOrderPayer


class TransferLineRead(BaseModel):
    id: uuid.UUID
    part_id: uuid.UUID
    quantity: int
    unit_price: float | None
    subtotal: float | None
    payer: ServiceOrderPayer


class TransferRead(BaseModel):
    id: uuid.UUID
    code: str
    status: TransferStatus
    lines: list[TransferLineRead]
    subtotal: float | None
    fulfilled_by_user_id: uuid.UUID | None = None
    fulfilled_at: datetime | None = None
    created_at: datetime


class PayerBreakdown(BaseModel):
    payer: ServiceOrderPayer
    labor_subtotal: float
    parts_subtotal: float
    subtotal: float


class OrderSummary(BaseModel):
    payer_breakdown: list[PayerBreakdown] | None = None
    igtf_percentage: float = 0
    igtf_amount: float = 0
    pricing_frozen: bool = False
    pricing_snapshot_available: bool = True
    discount_label: DiscountLabel
    tasks: list[TaskRead]
    transfers: list[TransferRead]
    parts_subtotal: float | None
    labor_subtotal: float | None
    non_client_subtotal: float = 0
    iva_percentage: float | None
    iva_amount: float | None
    total: float
    # Non-blocking, one-off hints for the request that produced this summary
    # (e.g. "added the part anyway, but stock is short") — never persisted,
    # empty on any summary read that isn't immediately after such an action.
    warnings: list[str] = Field(default_factory=list)


class UpsellTaskInput(BaseModel):
    tempario_id: uuid.UUID


class UpsellPartInput(BaseModel):
    part_id: uuid.UUID
    quantity: int = Field(gt=0)


class UpsellCreate(BaseModel):
    title: str = Field(min_length=2, max_length=150)
    description: str = Field(min_length=2)
    evidence_count: int = Field(default=0, ge=0)
    detected_by_user_id: uuid.UUID | None = None
    tasks: list[UpsellTaskInput] = Field(default_factory=list)
    parts: list[UpsellPartInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _requires_at_least_one_line(self) -> "UpsellCreate":
        if not self.tasks and not self.parts:
            raise ValueError("Agrega al menos una tarea del tempario o un repuesto.")
        return self


class UpsellDecisionInput(BaseModel):
    status: Literal["aprobado", "rechazado", "pospuesto"]
    # Only meaningful (and required) for status="aprobado" — how the client
    # actually agreed to pay for the additional work.
    approval_channel: UpsellApprovalChannel | None = None

    @model_validator(mode="after")
    def _requires_channel_when_approved(self) -> "UpsellDecisionInput":
        if self.status == "aprobado" and self.approval_channel is None:
            raise ValueError("Indica por qué medio el cliente aprobó el trabajo adicional.")
        return self


class UpsellTaskRead(BaseModel):
    id: uuid.UUID
    tempario_id: uuid.UUID
    code_snapshot: str
    name_snapshot: str
    hours_snapshot: float


class UpsellPartRead(BaseModel):
    id: uuid.UUID
    part_id: uuid.UUID
    name_snapshot: str
    quantity: int
    unit_cost_snapshot: float
    line_total: float


class UpsellRead(BaseModel):
    id: uuid.UUID
    service_order_id: uuid.UUID
    title: str
    description: str
    detected_by_user_id: uuid.UUID | None
    evidence_count: int
    status: UpsellStatus
    tasks: list[UpsellTaskRead]
    parts: list[UpsellPartRead]
    # A preview: labor at the filial's current hourly rate + parts at the
    # cost they had when proposed, marked up by the order's own discount
    # tier — the same formula that will actually price them on approval,
    # give or take whatever moved (rate, stock cost) since this was built.
    amount: float
    approved_by_user_id: uuid.UUID | None
    approval_channel: UpsellApprovalChannel | None
    created_at: datetime
    resolved_at: datetime | None


class WarrantyClaimCreate(BaseModel):
    claim_type: WarrantyClaimType
    vehicle_id: uuid.UUID
    # Mandatory for claim_type=comeback (enforced below), optional otherwise.
    service_order_id: uuid.UUID | None = None
    tempario_id: uuid.UUID | None = None
    part_id: uuid.UUID | None = None
    failure_category: ReworkFailureCategory | None = None
    failure_cause: str | None = Field(default=None, max_length=300)
    reported_symptom: str | None = Field(default=None, max_length=1000)
    reported_mileage: int = Field(ge=0)
    claimed_at: date = Field(default_factory=date.today)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _check_fields_for_claim_type(self) -> "WarrantyClaimCreate":
        if self.claim_type == WarrantyClaimType.COMEBACK and self.service_order_id is None:
            raise ValueError("Un reclamo de tipo comeback debe referenciar la orden de servicio de origen.")
        if self.claim_type in (WarrantyClaimType.COMEBACK, WarrantyClaimType.REPUESTO_PROVEEDOR):
            if not self.failure_cause or len(self.failure_cause.strip()) < 3:
                raise ValueError("Describe la causa de la falla.")
        else:
            if not self.reported_symptom or len(self.reported_symptom.strip()) < 3:
                raise ValueError("Describe el síntoma reportado.")
        return self


class WarrantyClaimAuthorizationInput(BaseModel):
    decision: Literal["aprobado", "rechazado"]
    # Only meaningful for claim_type in (fabrica, campana_recall) — the
    # explicit, recorded exception to authorize without a vigente factory
    # warranty on the vehicle. Ignored on rejection.
    warranty_override: bool = False
    warranty_override_note: str | None = Field(default=None, max_length=500)
    # Only meaningful for claim_type in (comeback, repuesto_proveedor) when
    # the claim wasn't created with one yet.
    failure_category: ReworkFailureCategory | None = None


class WarrantyClaimConvertInput(BaseModel):
    # For the new order this step opens — kept minimal, auto-derived from
    # the original order (if any) or the claim's own reported_mileage when
    # omitted (see convert_warranty_claim_to_order).
    intake_mileage: int | None = Field(default=None, ge=0)
    promised_at: date | None = None


class WarrantyClaimRead(BaseModel):
    id: uuid.UUID
    code: str
    filial_id: uuid.UUID
    claim_type: WarrantyClaimType
    vehicle_id: uuid.UUID
    vehicle_plate: str | None
    vehicle_vin: str | None
    client_name: str
    service_order_id: uuid.UUID | None
    service_order_code: str | None
    tempario_id: uuid.UUID | None
    tempario_name: str | None
    part_id: uuid.UUID | None
    part_name: str | None
    failure_category: ReworkFailureCategory | None
    failure_cause: str | None
    reported_symptom: str | None
    reported_mileage: int
    vehicle_mileage_at_claim: int | None
    mileage_inconsistent: bool
    claimed_at: date
    recorded_by_user_id: uuid.UUID | None
    note: str | None
    photo_urls: list[str]
    document_urls: list[str]
    created_at: datetime
    status: WarrantyClaimStatus
    # Populated only once a repuesto_proveedor claim is converted — either
    # the ids of the SupplierClaim(s) generated automatically, or a note
    # explaining why none could be (e.g. the lot predates this feature and
    # has no purchase order on record).
    auto_generated_supplier_claim_ids: list[uuid.UUID] = Field(default_factory=list)
    supplier_claim_note: str | None = None
    authorized_by_user_id: uuid.UUID | None
    authorized_at: datetime | None
    warranty_override: bool
    warranty_override_note: str | None
    converted_by_user_id: uuid.UUID | None
    converted_at: datetime | None
    resulting_service_order_id: uuid.UUID | None
    resulting_service_order_code: str | None = None


class WarrantyClaimContext(BaseModel):
    """Everything an advisor needs to verify what the system claims about a
    vehicle before filing a warranty claim against it, instead of trusting a
    bare pass/fail message."""

    current_mileage: int | None
    last_visit_date: date | None
    last_visit_service_order_code: str | None
    factory_warranty: VehicleWarrantyRead | None = None
    workshop_warranties: list[WorkshopWarrantyRead] = Field(default_factory=list)
    duplicate_open_claim: WarrantyClaimRead | None = None
