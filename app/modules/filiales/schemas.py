import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FiscalIdentity(BaseModel):
    legal_name: str | None = Field(default=None, min_length=2, max_length=200)
    rif: str | None = Field(default=None, pattern=r"^[VEJPG]-?\d{8}-?\d$")
    fiscal_address: str | None = Field(default=None, min_length=5, max_length=500)
    business_type: Literal["concesionario", "taller", "mixto"] | None = None
    represented_brands: str | None = Field(default=None, max_length=300)
    special_taxpayer: bool = False
    invoice_series: str | None = Field(
        default=None, min_length=1, max_length=20, pattern=r"^[A-Za-z0-9-]+$"
    )


class FilialBase(FiscalIdentity):
    name: str = Field(min_length=2, max_length=150)
    slug: str = Field(min_length=2, max_length=150, pattern=r"^[a-z0-9-]+$")


class FilialCreate(FilialBase):
    holding_id: uuid.UUID


class FilialUpdate(BaseModel):
    legal_name: str | None = Field(default=None, min_length=2, max_length=200)
    rif: str | None = Field(default=None, pattern=r"^[VEJPG]-?\d{8}-?\d$")
    fiscal_address: str | None = Field(default=None, min_length=5, max_length=500)
    business_type: Literal["concesionario", "taller", "mixto"] | None = None
    represented_brands: str | None = Field(default=None, max_length=300)
    special_taxpayer: bool | None = None
    invoice_series: str | None = Field(
        default=None, min_length=1, max_length=20, pattern=r"^[A-Za-z0-9-]+$"
    )
    name: str | None = Field(default=None, min_length=2, max_length=150)
    slug: str | None = Field(default=None, min_length=2, max_length=150, pattern=r"^[a-z0-9-]+$")


class FilialRead(FilialBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    holding_id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime
