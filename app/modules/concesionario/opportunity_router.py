import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.clients.schemas import LeadOrigin
from app.modules.concesionario.opportunities import OpportunityService, read_opportunity
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access

router = APIRouter(prefix="/vehicle-opportunities", tags=["Vehicle opportunities"])


class OpportunityCreate(BaseModel):
    filial_id: uuid.UUID
    client_id: uuid.UUID
    advisor_user_id: uuid.UUID | None = None
    origin: LeadOrigin = "registro_directo"
    notes: str | None = Field(default=None, max_length=3000)


class OpportunityUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    advisor_user_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=3000)
    next_contact_at: date | None = None
    stage: Literal["prospecto", "financiamiento", "perdida"] | None = None
    lost_reason: str | None = Field(default=None, max_length=1000)


class OpportunityQuote(BaseModel):
    discount_percentage: float = Field(default=0, ge=0, le=100, allow_inf_nan=False)
    expected_version: int = Field(ge=1)
    vehicle_id: uuid.UUID
    sale_type: Literal["contado", "financiado"] = "contado"


class OpportunityDelivery(BaseModel):
    confirmed: Literal[True]
    reference: str = Field(min_length=3, max_length=500)

    @field_validator("reference")
    @classmethod
    def non_blank(cls, value):
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Indica una constancia de entrega válida.")
        return value


async def access(db, user, filial, edit=False):
    await ensure_module_access(
        db, user, filial, "concesionario", AccessLevel.EDITAR if edit else AccessLevel.VER
    )


@router.get("")
async def board(
    filial_id: uuid.UUID,
    search: str | None = Query(None, max_length=150),
    advisor_user_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await access(db, user, filial_id)
    return await OpportunityService(db).board(filial_id, search, advisor_user_id, page, page_size)


@router.post("", status_code=201)
async def create(
    payload: OpportunityCreate,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await access(db, user, payload.filial_id, True)
    return await OpportunityService(db).create(payload, user)


@router.get("/rules/current")
async def sales_rules(
    filial_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await access(db, user, filial_id)
    from app.modules.post_ventas.service import PostVentasService

    settings = await PostVentasService(db).get_labor_settings(filial_id)
    return {
        key: float(getattr(settings, key))
        for key in (
            "vehicle_quote_valid_days",
            "vehicle_reservation_days",
            "vehicle_min_deposit_percentage",
            "vehicle_discount_without_approval_percentage",
            "vehicle_withdrawal_retention_percentage",
        )
    }


@router.get("/{opportunity_id}")
async def detail(
    opportunity_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    opp = await OpportunityService(db).get(opportunity_id)
    await access(db, user, opp.filial_id)
    return read_opportunity(opp)


@router.patch("/{opportunity_id}")
async def update(
    opportunity_id: uuid.UUID,
    payload: OpportunityUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = OpportunityService(db)
    opp = await service.get(opportunity_id)
    await access(db, user, opp.filial_id, True)
    return await service.update(opportunity_id, payload, user)


@router.post("/{opportunity_id}/quote")
async def quote(
    opportunity_id: uuid.UUID,
    payload: OpportunityQuote,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = OpportunityService(db)
    opp = await service.get(opportunity_id)
    await access(db, user, opp.filial_id, True)
    return await service.quote(opportunity_id, payload, user)


@router.post("/{opportunity_id}/deliver")
async def deliver(
    opportunity_id: uuid.UUID,
    payload: OpportunityDelivery,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = OpportunityService(db)
    opp = await service.get(opportunity_id)
    await access(db, user, opp.filial_id, True)
    return await service.deliver(opportunity_id, payload, user)
