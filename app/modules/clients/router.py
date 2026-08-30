import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.clients.schemas import ClientCreate, ClientRead, ClientUpdate
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