import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.parts.pricing import DEFAULT_DISCOUNT, DiscountLabel
from app.modules.service_orders.enums import (
    ReworkAuthorizationStatus,
    ReworkClaimStatus,
    ReworkFailureCategory,
    ServiceOrderPayer,
    ServiceOrderStatus,
    ServiceOrderType,
    TaskStatus,
    TransferStatus,
    UpsellStatus,
    WarrantyClaimStatus,
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


class UpsellCreate(BaseModel):
    title: str = Field(min_length=2, max_length=150)
    description: str = Field(min_length=2)
    evidence_count: int = Field(default=0, ge=0)
    detected_by_user_id: uuid.UUID | None = None


class UpsellStatusUpdate(BaseModel):
    status: UpsellStatus


class UpsellRead(BaseModel):
    id: uuid.UUID
    service_order_id: uuid.UUID
    title: str
    description: str
    detected_by_user_id: uuid.UUID | None
    evidence_count: int
    status: UpsellStatus
    created_at: datetime
    resolved_at: datetime | None


class ReworkClaimCreate(BaseModel):
    tempario_id: uuid.UUID | None = None
    part_id: uuid.UUID | None = None
    # Optional here — the cause may genuinely not be known yet the day the
    # client complains. It's required to close the claim (see ReworkClaimCloseInput).
    failure_category: ReworkFailureCategory | None = None
    failure_cause: str = Field(min_length=3, max_length=300)
    claimed_at: date = Field(default_factory=date.today)
    note: str | None = Field(default=None, max_length=500)


class ReworkClaimCloseInput(BaseModel):
    # Optional only because the claim may already carry a category from
    # creation — omit it here to close with whatever's already on record.
    # Closing with neither set raises FailureCategoryRequiredError.
    failure_category: ReworkFailureCategory | None = None
    note: str | None = Field(default=None, max_length=500)


class ReworkClaimAuthorizationInput(BaseModel):
    decision: Literal["aprobado", "rechazado"]
    # The explicit, recorded exception to authorize without a vigente
    # factory warranty on the vehicle — ignored on rejection.
    warranty_override: bool = False
    warranty_override_note: str | None = Field(default=None, max_length=500)
    # For the new order this decision opens — kept minimal, auto-derived
    # from the original order when omitted (see the service method).
    intake_mileage: int | None = Field(default=None, ge=0)
    promised_at: date | None = None


class ReworkClaimRead(BaseModel):
    id: uuid.UUID
    service_order_id: uuid.UUID
    tempario_id: uuid.UUID | None
    tempario_name: str | None
    part_id: uuid.UUID | None
    part_name: str | None
    failure_category: ReworkFailureCategory | None
    failure_cause: str
    claimed_at: date
    days_since_invoice: int | None
    recorded_by_user_id: uuid.UUID | None
    note: str | None
    created_at: datetime
    status: ReworkClaimStatus
    closed_at: datetime | None
    closed_by_user_id: uuid.UUID | None
    # Populated only once the claim is closed with failure_category
    # repuesto_defectuoso — either the ids of the SupplierClaim(s) generated
    # automatically, or a note explaining why none could be (e.g. the lot
    # predates this feature and has no purchase order on record).
    auto_generated_supplier_claim_ids: list[uuid.UUID] = Field(default_factory=list)
    supplier_claim_note: str | None = None
    authorization_status: ReworkAuthorizationStatus
    authorized_by_user_id: uuid.UUID | None
    authorized_at: datetime | None
    warranty_override: bool
    warranty_override_note: str | None
    resulting_service_order_id: uuid.UUID | None
    resulting_service_order_code: str | None = None


class WarrantyClaimCreate(BaseModel):
    vehicle_id: uuid.UUID
    warranty_ids: list[uuid.UUID] = Field(min_length=1)
    reported_symptom: str = Field(min_length=3, max_length=1000)
    reported_mileage: int = Field(ge=0)


class WarrantyClaimRead(BaseModel):
    id: uuid.UUID
    filial_id: uuid.UUID
    vehicle_id: uuid.UUID
    vehicle_plate: str
    vehicle_vin: str | None
    client_name: str
    reported_symptom: str
    reported_mileage: int
    vehicle_mileage_at_claim: int | None
    mileage_inconsistent: bool
    status: WarrantyClaimStatus
    warranty_ids: list[uuid.UUID]
    recorded_by_user_id: uuid.UUID | None
    created_at: datetime
