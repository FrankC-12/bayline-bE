import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class HoldingBase(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    slug: str = Field(min_length=2, max_length=150, pattern=r"^[a-z0-9-]+$")


class HoldingCreate(HoldingBase):
    pass


class HoldingUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    slug: str | None = Field(default=None, min_length=2, max_length=150, pattern=r"^[a-z0-9-]+$")


class HoldingRead(HoldingBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime


class OdsSummary(BaseModel):
    total: int
    pendiente: int
    en_progreso: int
    completado: int
    orden_cerrada: int
    cancelado: int


class OdtSummary(BaseModel):
    total: int
    pendiente: int
    pedido: int


class SalesSummary(BaseModel):
    count: int
    total_usd: float


class FilialDashboardRow(BaseModel):
    filial_id: uuid.UUID
    filial_name: str
    ods: OdsSummary
    odt: OdtSummary
    ventas_repuestos: SalesSummary
    ventas_vehiculos: SalesSummary
    clientes: int
    usuarios_total: int
    usuarios_activos: int
    almacenes_total: int
    almacenes_activos: int


class HoldingDashboardReport(BaseModel):
    holding_id: uuid.UUID
    filiales: list[FilialDashboardRow]
