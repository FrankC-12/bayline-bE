import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.clients.models import Client
from app.modules.clients.schemas import (
    ClientCreate,
    ClientRead,
    ClientUpdate,
    VehicleMaintenancePlanAssign,
    VehicleMileageHistoryEntry,
    VehiclePlanStatusRead,
)
from app.modules.clients.service import ClientService
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access

MODULE_ID = "clientes-vehiculos"

router = APIRouter(prefix="/clients", tags=["Clients"])


def get_client_service(db: AsyncSession = Depends(get_db)) -> ClientService:
    return ClientService(db)


async def _ensure_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, MODULE_ID, level)


@router.get("", response_model=list[ClientRead])
async def list_clients(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: ClientService = Depends(get_client_service),
) -> list[ClientRead]:
    """List clients for a filial, optionally filtered by a free-text search."""
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_clients(filial_id, search)


@router.get("/{client_id}", response_model=ClientRead)
async def get_client(
    client_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ClientService = Depends(get_client_service),
) -> ClientRead:
    """Retrieve a single client with its vehicles."""
    client = await service.get_client(client_id)
    await _ensure_access(current_user, client.filial_id, service.db)
    return client


@router.post("", response_model=ClientRead, status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: ClientCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ClientService = Depends(get_client_service),
) -> ClientRead:
    """Create a client, optionally with its vehicles, within your filial."""
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_client(payload)


@router.patch("/{client_id}", response_model=ClientRead)
async def update_client(
    client_id: uuid.UUID,
    payload: ClientUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ClientService = Depends(get_client_service),
) -> ClientRead:
    """Update a client's data and/or reconcile its list of vehicles."""
    existing = await service.get_client(client_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_client(client_id, payload)


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(
    client_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ClientService = Depends(get_client_service),
) -> None:
    """Delete a client and its vehicles."""
    existing = await service.get_client(client_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    await service.delete_client(client_id)


async def _vehicle_filial_id(service: ClientService, vehicle_id: uuid.UUID) -> uuid.UUID:
    vehicle = await service.get_vehicle(vehicle_id)
    client = await service.db.get(Client, vehicle.client_id)
    return client.filial_id


@router.get("/vehicles/{vehicle_id}/maintenance-plan-status", response_model=VehiclePlanStatusRead)
async def get_vehicle_plan_status(
    vehicle_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ClientService = Depends(get_client_service),
) -> VehiclePlanStatusRead:
    """Live status (pendiente/vencido/cumplido/omitido) of every entry in the
    vehicle's assigned maintenance plan."""
    filial_id = await _vehicle_filial_id(service, vehicle_id)
    await _ensure_access(current_user, filial_id, service.db)
    return await service.get_vehicle_plan_status(vehicle_id)


@router.get("/vehicles/{vehicle_id}/mileage-history", response_model=list[VehicleMileageHistoryEntry])
async def get_vehicle_mileage_history(
    vehicle_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ClientService = Depends(get_client_service),
) -> list[VehicleMileageHistoryEntry]:
    """Full history of recorded odometer readings for this vehicle, newest
    first — one entry per preliminary inspection that captured a mileage,
    never overwritten."""
    filial_id = await _vehicle_filial_id(service, vehicle_id)
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_mileage_history(vehicle_id)


@router.patch("/vehicles/{vehicle_id}/maintenance-plan", response_model=VehiclePlanStatusRead)
async def assign_vehicle_maintenance_plan(
    vehicle_id: uuid.UUID,
    payload: VehicleMaintenancePlanAssign,
    current_user: CurrentUser = Depends(get_current_user),
    service: ClientService = Depends(get_client_service),
) -> VehiclePlanStatusRead:
    """Assigns (or clears, with plan_id=null) the maintenance plan this
    vehicle follows."""
    filial_id = await _vehicle_filial_id(service, vehicle_id)
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    return await service.assign_maintenance_plan(vehicle_id, payload.plan_id)