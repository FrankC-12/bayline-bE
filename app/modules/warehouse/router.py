import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.warehouse.schemas import (
    BulkLotCreate,
    BulkLotReview,
    BulkLotResult,
    InventoryRow,
    PartLotRead,
    StockInCreate,
    StockMovementRead,
    StockOutCreate,
    TransferCreate,
    TransferRead,
    TransferStatusUpdate,
    WarehouseCreate,
    WarehouseRead,
    WarehouseUpdate,
)
from app.modules.warehouse.service import AlmacenService, _lot_to_read, transfer_to_read
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access

MODULE_ID = "almacen"

router = APIRouter(tags=["Almacen"])


def get_service(db: AsyncSession = Depends(get_db)) -> AlmacenService:
    return AlmacenService(db)


async def _ensure_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, MODULE_ID, level)


@router.get("/warehouses", response_model=list[WarehouseRead])
async def list_warehouses(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> list[WarehouseRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_warehouses(filial_id)


@router.post("/warehouses", response_model=WarehouseRead, status_code=status.HTTP_201_CREATED)
async def create_warehouse(
    payload: WarehouseCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> WarehouseRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_warehouse(payload.filial_id, payload.name)


@router.patch("/warehouses/{warehouse_id}", response_model=WarehouseRead)
async def update_warehouse(
    warehouse_id: uuid.UUID,
    payload: WarehouseUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> WarehouseRead:
    existing = await service.get_warehouse(warehouse_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_warehouse(warehouse_id, payload.name, payload.is_active)


@router.get("/almacen/inventory", response_model=list[InventoryRow])
async def get_inventory(
    filial_id: uuid.UUID = Query(...),
    warehouse_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> list[InventoryRow]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.get_inventory(filial_id, warehouse_id, search)


@router.get("/almacen/lots", response_model=list[PartLotRead])
async def list_lots(
    filial_id: uuid.UUID = Query(...),
    part_id: uuid.UUID | None = Query(default=None),
    warehouse_id: uuid.UUID | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> list[PartLotRead]:
    await _ensure_access(current_user, filial_id, service.db)
    lots = await service.list_lots(filial_id, part_id, warehouse_id)
    return [_lot_to_read(lot) for lot in lots]


@router.post("/almacen/stock-in", response_model=list[PartLotRead], status_code=status.HTTP_201_CREATED)
async def create_stock_in(
    payload: StockInCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> list[PartLotRead]:
    """Registrar entrada: one or more lines, each creates its own FIFO lot."""
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    lots = await service.create_stock_in(payload, current_user.user_id)
    return [_lot_to_read(lot) for lot in lots]


@router.post("/almacen/stock-in/bulk", response_model=BulkLotResult, status_code=status.HTTP_201_CREATED)
async def bulk_create_lots(
    payload: BulkLotCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> BulkLotResult:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    created, skipped = await service.bulk_create_lots(
        payload.filial_id, payload.warehouse_id, payload.items, current_user.user_id
    )
    return BulkLotResult(created=[_lot_to_read(lot) for lot in created], skipped=skipped)


@router.post("/almacen/stock-in/bulk/review", response_model=BulkLotReview)
async def review_bulk_lots(
    payload: BulkLotCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> BulkLotReview:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.review_bulk_lots(payload.filial_id, payload.items)


@router.post("/almacen/stock-out", status_code=status.HTTP_201_CREATED)
async def create_stock_out(
    payload: StockOutCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> dict[str, str]:
    """Registrar salida: manual stock-out (consumption, adjustment, or a
    return to a supplier), consuming FIFO lots."""
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    await service.create_stock_out(payload, current_user.user_id)
    return {"status": "ok"}


@router.get("/almacen/transfers", response_model=list[TransferRead])
async def list_transfers(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> list[TransferRead]:
    await _ensure_access(current_user, filial_id, service.db)
    transfers = await service.list_transfers(filial_id)
    return [transfer_to_read(t) for t in transfers]


@router.get("/almacen/transfers/{transfer_id}", response_model=TransferRead)
async def get_transfer(
    transfer_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> TransferRead:
    transfer = await service.get_transfer(transfer_id)
    await _ensure_access(current_user, transfer.filial_id, service.db)
    return transfer_to_read(transfer)


@router.post("/almacen/transfers", response_model=TransferRead, status_code=status.HTTP_201_CREATED)
async def create_transfer(
    payload: TransferCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> TransferRead:
    """Creates an ODT in 'Pedido' status. Stock doesn't move until it's
    marked 'Completada'."""
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    transfer = await service.create_transfer(payload)
    return transfer_to_read(transfer)


@router.patch("/almacen/transfers/{transfer_id}", response_model=TransferRead)
async def update_transfer_status(
    transfer_id: uuid.UUID,
    payload: TransferStatusUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> TransferRead:
    existing = await service.get_transfer(transfer_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    transfer = await service.update_transfer_status(transfer_id, payload.status, current_user.user_id)
    return transfer_to_read(transfer)


@router.get("/almacen/movements", response_model=list[StockMovementRead])
async def list_movements(
    filial_id: uuid.UUID = Query(...),
    part_id: uuid.UUID | None = Query(default=None),
    warehouse_id: uuid.UUID | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> list[StockMovementRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_movements(filial_id, part_id, warehouse_id)
