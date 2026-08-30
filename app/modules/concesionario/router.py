import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.concesionario.schemas import (
    VehicleCreate,
    VehicleRead,
    VehicleSaleRead,
    VehicleUpdate,
)
from app.modules.concesionario.service import ConcesionarioService
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access

MODULE_ID = "concesionario"

router = APIRouter(tags=["Concesionario"])


def get_service(db: AsyncSession = Depends(get_db)) -> ConcesionarioService:
    return ConcesionarioService(db)


async def _ensure_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, MODULE_ID, level)


@router.get("/dealership-vehicles", response_model=list[VehicleRead])
async def list_vehicles(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: ConcesionarioService = Depends(get_service),
) -> list[VehicleRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_vehicles(filial_id, search)


@router.post("/dealership-vehicles", response_model=VehicleRead, status_code=status.HTTP_201_CREATED)
async def create_vehicle(
    payload: VehicleCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ConcesionarioService = Depends(get_service),
) -> VehicleRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_vehicle(payload)


@router.patch("/dealership-vehicles/{vehicle_id}", response_model=VehicleRead)
async def update_vehicle(
    vehicle_id: uuid.UUID,
    payload: VehicleUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ConcesionarioService = Depends(get_service),
) -> VehicleRead:
    existing = await service.get_vehicle(vehicle_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_vehicle(vehicle_id, payload)


@router.delete("/dealership-vehicles/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vehicle(
    vehicle_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ConcesionarioService = Depends(get_service),
) -> None:
    existing = await service.get_vehicle(vehicle_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    await service.delete_vehicle(vehicle_id)


@router.get("/vehicle-sales", response_model=list[VehicleSaleRead])
async def list_vehicle_sales(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: ConcesionarioService = Depends(get_service),
) -> list[VehicleSaleRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_sales(filial_id)