import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.kpis.schemas import KpiReport
from app.modules.kpis.service import KpiService
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access

MODULE_ID = "kpis"

router = APIRouter(tags=["KPIs"])


def get_service(db: AsyncSession = Depends(get_db)) -> KpiService:
    return KpiService(db)


async def _ensure_access(current_user: CurrentUser, filial_id: uuid.UUID, db: AsyncSession) -> None:
    await ensure_module_access(db, current_user, filial_id, MODULE_ID, AccessLevel.VER)


@router.get("/kpis/tecnicos", response_model=KpiReport)
async def get_technician_kpis(
    filial_id: uuid.UUID = Query(...),
    date_from: date = Query(...),
    date_to: date = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: KpiService = Depends(get_service),
) -> KpiReport:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.get_technician_kpis(filial_id, date_from, date_to)


@router.get("/kpis/asesores", response_model=KpiReport)
async def get_advisor_kpis(
    filial_id: uuid.UUID = Query(...),
    date_from: date = Query(...),
    date_to: date = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: KpiService = Depends(get_service),
) -> KpiReport:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.get_advisor_kpis(filial_id, date_from, date_to)


@router.get("/kpis/almacenistas", response_model=KpiReport)
async def get_warehouse_kpis(
    filial_id: uuid.UUID = Query(...),
    date_from: date = Query(...),
    date_to: date = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: KpiService = Depends(get_service),
) -> KpiReport:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.get_warehouse_kpis(filial_id, date_from, date_to)