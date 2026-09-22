import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.warehouse.enums import MovementReason, MovementType, TransferStatus


class WarehouseCreate(BaseModel):
    filial_id: uuid.UUID
    name: str = Field(min_length=1, max_length=80)


class WarehouseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    is_active: bool | None = None


class WarehouseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    name: str
    is_active: bool
    created_at: datetime


class StockInReasonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class StockInReasonUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)


class StockInReasonRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    holding_id: uuid.UUID
    name: str
    is_active: bool


class LotLineInput(BaseModel):
    part_id: uuid.UUID
    quantity: int = Field(ge=1)
    unit_cost: float = Field(ge=0)
    location: str | None = None
    purchase_request_id: uuid.UUID | None = None


class StockInCreate(BaseModel):
    filial_id: uuid.UUID
    warehouse_id: uuid.UUID
    reason: str = Field(default="Compra directa", max_length=80)
    lines: list[LotLineInput] = Field(min_length=1)


class PartLotRead(BaseModel):
    id: uuid.UUID
    code: str
    warehouse_id: uuid.UUID
    part_id: uuid.UUID
    quantity_received: int
    quantity_remaining: int
    unit_cost: float
    location: str | None
    note: str | None
    received_at: datetime


class LotOutboundMovementRead(BaseModel):
    """One dispatched consumption of this lot — a counter sale line or a
    workshop ODT line. Only real, dispatched consumption is included (never
    a pending/preview allocation)."""

    id: uuid.UUID
    source: str  # "venta_repuestos" | "odt_taller"
    quantity: int
    unit_cost: float
    occurred_at: datetime
    reference_code: str
    description: str
    # Opaque id of whatever the reference_code points to (a PartSale or a
    # ServiceOrder) — lets the frontend link to its detail screen.
    link_id: uuid.UUID


class PartLotDetailRead(PartLotRead):
    part_code: str
    part_name: str
    warehouse_name: str
    outbound_movements: list[LotOutboundMovementRead]


class ServiceOrderPartRequestLineWarehouse(BaseModel):
    """How much of a line's quantity was actually pulled from one specific
    warehouse — an ODT line isn't scoped to a single warehouse the way a
    counter sale is, so a line can (rarely) split across more than one."""

    warehouse_id: uuid.UUID
    warehouse_name: str
    quantity: int


class ServiceOrderPartRequestLineRead(BaseModel):
    part_id: uuid.UUID
    part_code: str
    part_name: str
    quantity: int
    warehouses: list[ServiceOrderPartRequestLineWarehouse]


class ServiceOrderPartRequestRead(BaseModel):
    """A dispatched ODT ('Marcar como Pedido' from a service order) surfaced
    to almacén staff — this is how a parts request travels from the ODS side
    to the 'Órdenes de Transferencia' screen. `warehouse_seen` drives the
    unseen-count badge on that screen's nav item."""

    id: uuid.UUID
    code: str
    service_order_id: uuid.UUID
    service_order_code: str
    vehicle_label: str
    status: str
    fulfilled_at: datetime | None
    completed_at: datetime | None
    warehouse_seen: bool
    lines: list[ServiceOrderPartRequestLineRead]


class PartSaleRequestLineRead(BaseModel):
    part_id: uuid.UUID
    part_code: str
    part_name: str
    quantity: int
    warehouse_id: uuid.UUID | None
    warehouse_name: str | None


class PartSaleRequestRead(BaseModel):
    """A counter parts sale (Venta de Repuestos) surfaced to almacén staff
    the same way a dispatched ODT is — its destination is the sales
    counter/mostrador, not a taller bay, which is exactly what almacén
    staff needs to tell apart at a glance from the Órdenes de Servicio
    requests in the same screen."""

    id: uuid.UUID
    code: str
    client_name: str
    status: str
    created_at: datetime
    lines: list[PartSaleRequestLineRead]


class BulkLotItem(BaseModel):
    part_code: str = Field(min_length=1, max_length=40)
    part_name: str = Field(min_length=1, max_length=150)
    category: str | None = Field(default=None, max_length=80)
    quantity: int = Field(ge=1)
    unit_cost: float = Field(ge=0)
    location: str | None = None


class BulkLotCreate(BaseModel):
    filial_id: uuid.UUID
    warehouse_id: uuid.UUID
    items: list[BulkLotItem] = Field(min_length=1, max_length=500)


class BulkLotResult(BaseModel):
    created: list[PartLotRead]
    skipped: list[str]


class BulkLotReviewItem(BulkLotItem):
    catalog_name: str | None = None


class BulkLotReview(BaseModel):
    existing: list[BulkLotReviewItem]
    new: list[BulkLotReviewItem]
    conflicts: list[BulkLotReviewItem]


class StockOutCreate(BaseModel):
    filial_id: uuid.UUID
    warehouse_id: uuid.UUID
    part_id: uuid.UUID
    quantity: int = Field(ge=1)
    reason: MovementReason = MovementReason.CONSUMO_ODS
    reference: str | None = Field(default=None, max_length=80)


class TransferLineInput(BaseModel):
    part_id: uuid.UUID
    quantity: int = Field(ge=1)


class TransferCreate(BaseModel):
    filial_id: uuid.UUID
    origin_warehouse_id: uuid.UUID
    destination_warehouse_id: uuid.UUID
    note: str | None = None
    lines: list[TransferLineInput] = Field(min_length=1)


class TransferStatusUpdate(BaseModel):
    status: TransferStatus


class TransferLineRead(BaseModel):
    id: uuid.UUID
    part_id: uuid.UUID
    quantity: int
    unit_cost: float
    subtotal: float


class TransferRead(BaseModel):
    id: uuid.UUID
    code: str
    origin_warehouse_id: uuid.UUID
    destination_warehouse_id: uuid.UUID
    status: TransferStatus
    note: str | None
    lines: list[TransferLineRead]
    total_cost: float
    created_at: datetime
    completed_at: datetime | None
    completed_by_user_id: uuid.UUID | None


class StockMovementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    warehouse_id: uuid.UUID
    part_id: uuid.UUID
    movement_type: MovementType
    quantity: int
    unit_cost: float | None
    reference: str | None
    note: str | None
    responsible_user_id: uuid.UUID | None
    created_at: datetime


class InventoryRow(BaseModel):
    part_id: uuid.UUID
    part_code: str
    part_name: str
    warehouse_id: uuid.UUID
    warehouse_name: str
    quantity: int
    fifo_unit_cost: float | None
    location: str | None
    min_stock: int
