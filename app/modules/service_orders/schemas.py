import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.parts.pricing import DEFAULT_DISCOUNT, DiscountLabel
from app.modules.post_ventas.schemas import VehicleWarrantyRead, WorkshopWarrantyRead
from app.modules.service_orders.enums import (
    ReworkFailureCategory,
    ServiceOrderPayer,
    ServiceOrderStatus,
    TaskStatus,
    TransferStatus,
    UpsellApprovalChannel,
    UpsellDiscardReason,
    UpsellSeverity,
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


class ServiceOrderTypeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_system: bool
    claim_type: WarrantyClaimType | None
    is_selectable: bool
    is_active: bool


class ServiceOrderTypeCreate(BaseModel):
    filial_id: uuid.UUID
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)


class ServiceOrderTypeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = None
    is_active: bool | None = None


class AcceptedUpsellInput(BaseModel):
    upsell_id: uuid.UUID
    approval_channel: UpsellApprovalChannel


class ServiceOrderCreate(BaseModel):
    accepted_upsells: list[AcceptedUpsellInput] = Field(default_factory=list, max_length=50)
    discount_label: DiscountLabel = DEFAULT_DISCOUNT
    filial_id: uuid.UUID
    vehicle_id: uuid.UUID
    # None resolves to this filial's "regular" system type — see
    # ServiceOrderService.create_order.
    order_type_id: uuid.UUID | None = None
    # Customer-reported symptom, independent from inspection findings.
    inspection_id: uuid.UUID | None = None
    customer_reason: str | None = Field(default=None, min_length=1, max_length=2000)
    # Required exactly when the chosen order_type_id's catalog row has a
    # claim_type set (garantia_fabrica/comeback/campana, by default) — must
    # be an AUTORIZADO WarrantyClaim for this same vehicle_id, of that
    # matching claim_type (see ServiceOrderService.create_order).
    warranty_claim_id: uuid.UUID | None = None
    advisor_user_id: uuid.UUID
    # Explicit delivery commitment; never inferred from start/creation time.
    promised_at: datetime | None = None
    notes: str | None = None
    # Used by "Agendar Orden de Servicio" in the Calendario view — optional
    # so a normal walk-in ODS (created from the kanban) can omit them.
    scheduled_at: datetime | None = None
    technician_user_id: uuid.UUID | None = None
    bay_id: uuid.UUID | None = None


class ServiceOrderUpdate(BaseModel):
    discount_label: DiscountLabel | None = None
    status: ServiceOrderStatus | None = None
    order_type_id: uuid.UUID | None = None
    technician_user_id: uuid.UUID | None = None
    advisor_user_id: uuid.UUID | None = None
    bay_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None
    promised_at: datetime | None = None
    clear_promised_at: bool = False
    notes: str | None = None
    # "None" above means "leave unchanged" — these flags are how the client
    # explicitly asks to clear a nullable assignment back to "Sin asignar".
    clear_technician: bool = False
    clear_advisor: bool = False
    clear_bay: bool = False
    # One policy for labor, one for parts, applied to the whole ODS — "None"
    # here means "leave unchanged", same convention as the other FKs above;
    # use the matching clear_* flag to explicitly unset one.
    labor_warranty_policy_id: uuid.UUID | None = None
    parts_warranty_policy_id: uuid.UUID | None = None
    clear_labor_warranty_policy: bool = False
    clear_parts_warranty_policy: bool = False


class ServiceOrderCancelInput(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class ServiceOrderCloseInput(BaseModel):
    exit_mileage: int | None = Field(default=None, ge=0)
    # Optional "next visit suggested" date for the vehicle, entered by the
    # advisor at close time — feeds the "mantenimiento por vencer" list.
    next_maintenance_due_at: date | None = None
    # Optional plan task (Tempario) the advisor flags as pending for the
    # vehicle's next visit — lets the next ODS offer to load it in one click.
    next_maintenance_tempario_id: uuid.UUID | None = None


class ServiceOrderRead(BaseModel):
    vehicle_received_at: datetime | None = None
    vehicle_received_by_user_id: uuid.UUID | None = None
    is_draft: bool = False
    draft_revision: int = 1
    creation_source: str = "mobile"
    submitted_at: datetime | None = None
    estimated_hours: float = 0
    workshop_stage: str = "en_revision"
    exit_mileage: int | None = None
    discount_label: DiscountLabel
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    code: str
    vehicle_id: uuid.UUID | None
    status: ServiceOrderStatus
    order_type: ServiceOrderTypeRead
    warranty_claim_id: uuid.UUID | None
    labor_warranty_policy_id: uuid.UUID | None
    parts_warranty_policy_id: uuid.UUID | None
    technician_user_id: uuid.UUID | None
    advisor_user_id: uuid.UUID | None
    bay_id: uuid.UUID | None
    notes: str | None
    scheduled_at: datetime | None
    intake_mileage: int | None
    customer_reason: str | None
    promised_at: datetime | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    completed_at: datetime | None
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


class ServiceOrderPageItem(ServiceOrderRead):
    client_name: str
    vehicle_label: str
    vehicle_plate: str | None
    technician_name: str | None


def _reject_manual_proveedor_payer(cls, value: ServiceOrderPayer) -> ServiceOrderPayer:
    if value == ServiceOrderPayer.PROVEEDOR:
        raise ValueError(
            "El proveedor ya no puede asignarse manualmente como responsable de pago; "
            "se asigna automáticamente solo al convertir un reclamo de garantía de tipo "
            "'Repuesto — proveedor'."
        )
    return value


class TaskCreate(BaseModel):
    tempario_id: uuid.UUID
    payer: ServiceOrderPayer = ServiceOrderPayer.CLIENTE

    _validate_payer = field_validator("payer")(_reject_manual_proveedor_payer)


class TaskStatusUpdate(BaseModel):
    status: TaskStatus
    reason: str | None = Field(default=None, min_length=3, max_length=2000)


class TaskPauseInput(BaseModel):
    reason: Literal["almuerzo", "otra_tarea", "esperando_repuesto"] | None = None
    notes: str | None = Field(default=None, max_length=2000)


class TaskNotesInput(BaseModel):
    notes: str = Field(max_length=5000)


class TransferNotReceivedInput(BaseModel):
    reason: Literal["faltante", "incorrecto", "danado", "otro"]
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_other_description(self):
        if self.reason == "otro" and not (self.notes and self.notes.strip()):
            raise ValueError("Describe el motivo de la incidencia.")
        return self


class TaskPayerUpdate(BaseModel):
    payer: ServiceOrderPayer

    _validate_payer = field_validator("payer")(_reject_manual_proveedor_payer)


class TaskRead(BaseModel):
    notes: str | None = None
    photo_urls: list[str] = Field(default_factory=list)
    cancellation_reason: str | None = None
    pause_reason: str | None = None
    pause_history: list[dict] = Field(default_factory=list)
    id: uuid.UUID
    tempario_id: uuid.UUID
    code_snapshot: str
    name_snapshot: str
    hours_snapshot: float
    status: TaskStatus
    payer: ServiceOrderPayer
    created_at: datetime
    timer_started_at: datetime | None
    timer_accumulated_seconds: int


class TransferLineInput(BaseModel):
    part_id: uuid.UUID
    quantity: int = Field(ge=1)
    payer: ServiceOrderPayer = ServiceOrderPayer.CLIENTE

    _validate_payer = field_validator("payer")(_reject_manual_proveedor_payer)


class TransferLinePayerUpdate(BaseModel):
    payer: ServiceOrderPayer

    _validate_payer = field_validator("payer")(_reject_manual_proveedor_payer)


class TransferLineQuantityUpdate(BaseModel):
    quantity: int = Field(ge=1)


class TransferLineRead(BaseModel):
    id: uuid.UUID
    part_id: uuid.UUID
    quantity: int
    unit_price: float | None
    subtotal: float | None
    payer: ServiceOrderPayer


class TransferRead(BaseModel):
    receipt_incident: dict | None = None
    receipt_incident_history: list[dict] = Field(default_factory=list)
    id: uuid.UUID
    code: str
    status: TransferStatus
    lines: list[TransferLineRead]
    subtotal: float | None
    warehouse_id: uuid.UUID | None = None
    preparation_started_at: datetime | None = None
    picked_up_at: datetime | None = None
    pickup_photo_url: str | None = None
    stock_deducted: bool = False
    fulfilled_by_user_id: uuid.UUID | None = None
    fulfilled_at: datetime | None = None
    completed_by_user_id: uuid.UUID | None = None
    completed_at: datetime | None = None
    created_at: datetime


class PayerBreakdown(BaseModel):
    payer: ServiceOrderPayer
    labor_subtotal: float
    parts_subtotal: float
    subtotal: float


class OrderSummary(BaseModel):
    hourly_rate: float | None = None
    commission_percentage: float | None = None
    technician_commission: float | None = None
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
    severity: UpsellSeverity
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
    # Only meaningful for status="aprobado" — which ODS to add the tasks/
    # parts to. Omit to keep today's behavior (the upsell's own order, same
    # visit); set explicitly when applying a pending/postponed recommendation
    # from a past visit to the ODS open right now (a different order, likely
    # of a vehicle that's since had its original order closed).
    target_service_order_id: uuid.UUID | None = None
    # Only meaningful (and required) for status="rechazado" — "Descartar"
    # requires a reason; "otro" additionally requires a note.
    discard_reason: UpsellDiscardReason | None = None
    discard_note: str | None = None

    @model_validator(mode="after")
    def _requires_channel_when_approved(self) -> "UpsellDecisionInput":
        if self.status == "aprobado" and self.approval_channel is None:
            raise ValueError("Indica por qué medio el cliente aprobó el trabajo adicional.")
        return self

    @model_validator(mode="after")
    def _requires_reason_when_discarded(self) -> "UpsellDecisionInput":
        if self.status == "rechazado":
            if self.discard_reason is None:
                raise ValueError("Indica el motivo por el que se descarta esta recomendación.")
            if self.discard_reason == UpsellDiscardReason.OTRO and not self.discard_note:
                raise ValueError("Describe el motivo en la nota cuando el motivo es 'Otro'.")
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
    resolved_by_user_id: uuid.UUID | None = None
    id: uuid.UUID
    service_order_id: uuid.UUID
    title: str
    description: str
    detected_by_user_id: uuid.UUID | None
    severity: UpsellSeverity
    detected_mileage: int | None
    photo_urls: list[str]
    status: UpsellStatus
    tasks: list[UpsellTaskRead]
    parts: list[UpsellPartRead]
    # Frozen at creation time (see Upsell.quoted_price_snapshot) — the
    # historical "precio cotizado", never recalculated on read. Rows
    # created before this field existed fall back to the old live preview
    # formula (current hourly rate + current discount tier).
    amount: float
    approved_by_user_id: uuid.UUID | None
    approval_channel: UpsellApprovalChannel | None
    applied_to_service_order_id: uuid.UUID | None
    discard_reason: UpsellDiscardReason | None
    discard_note: str | None
    created_at: datetime
    resolved_at: datetime | None


class PendingUpsellRead(UpsellRead):
    """An UpsellRead plus which ODS it originated from — used only by the
    "recomendaciones pendientes de este vehículo" block, since that list
    spans multiple orders and needs to say where each one came from."""

    origin_service_order_code: str


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
            raise ValueError(
                "Un reclamo de tipo comeback debe referenciar la orden de servicio de origen."
            )
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
    promised_at: datetime | None = None


class WarrantyClaimFromWarranty(BaseModel):
    vehicle_id: uuid.UUID
    workshop_warranty_id: uuid.UUID | None = None
    factory_warranty_id: uuid.UUID | None = None
    campaign: bool = False
    reported_mileage: int = Field(ge=0)
    symptom: str = Field(min_length=3, max_length=2000)

    @model_validator(mode="after")
    def one_coverage(self):
        if (
            sum([bool(self.workshop_warranty_id), bool(self.factory_warranty_id), self.campaign])
            != 1
        ):
            raise ValueError("Selecciona una garantía o campaña.")
        return self


class ClaimReviewInput(BaseModel):
    decision: Literal["aprobado", "rechazado"]
    rejection_reason: str | None = Field(default=None, max_length=2000)
    failure_category: ReworkFailureCategory | None = None

    @model_validator(mode="after")
    def rejection_requires_reason(self):
        if self.decision == "rechazado" and not (self.rejection_reason or "").strip():
            raise ValueError("Indica el motivo del rechazo.")
        return self


class WarrantyClaimRead(BaseModel):
    vehicle_label: str | None = None
    coverage_snapshot: dict | None = None
    brand_responded_by_user_id: uuid.UUID | None = None
    workshop_warranty_id: uuid.UUID | None = None
    factory_warranty_id: uuid.UUID | None = None
    rejection_reason: str | None = None
    brand_status: str | None = None
    brand_sent_at: datetime | None = None
    brand_response_at: datetime | None = None
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


class ServiceOrderDraftCreate(BaseModel):
    filial_id: uuid.UUID
    vehicle_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None
    bay_id: uuid.UUID | None = None
    upsell_id: uuid.UUID | None = None


class ServiceOrderDraftUpdate(BaseModel):
    expected_revision: int | None = None
    vehicle_id: uuid.UUID | None = None
    technician_user_id: uuid.UUID | None = None
    bay_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None
    customer_reason: str | None = Field(default=None, max_length=2000)
