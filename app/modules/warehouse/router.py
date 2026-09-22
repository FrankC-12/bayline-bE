import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.filiales.exceptions import FilialNotFoundError
from app.modules.filiales.models import Filial
from app.modules.warehouse.schemas import (
    BulkLotCreate,
    BulkLotReview,
    BulkLotResult,
    InventoryRow,
    PartLotDetailRead,
    PartLotRead,
    PartSaleRequestRead,
    ServiceOrderPartRequestRead,
    StockInCreate,
    StockInReasonCreate,
    StockInReasonRead,
    StockInReasonUpdate,
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
# Motivos de Entrada is managed from Ajustes, same permission boundary as the
# other holding-wide catalogs (Marcas y Modelos, Categorías/Medidas de Repuestos).
AJUSTES_MODULE_ID = "ajustes"

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


async def _ensure_ajustes_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, AJUSTES_MODULE_ID, level)


async def _holding_id_for_filial(db: AsyncSession, filial_id: uuid.UUID) -> uuid.UUID:
    filial = await db.get(Filial, filial_id)
    if filial is None:
        raise FilialNotFoundError(str(filial_id))
    return filial.holding_id


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
    part_id: uuid.UUID | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> list[InventoryRow]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.get_inventory(filial_id, warehouse_id, search, part_id)


@router.get("/almacen/lots", response_model=list[PartLotRead])
async def list_lots(
    filial_id: uuid.UUID = Query(...),
    part_id: uuid.UUID | None = Query(default=None),
    warehouse_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None, description="Matches a lot code, e.g. 'L-104'."),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> list[PartLotRead]:
    await _ensure_access(current_user, filial_id, service.db)
    lots = await service.list_lots(filial_id, part_id, warehouse_id, search)
    return [_lot_to_read(lot) for lot in lots]


@router.get("/almacen/lots/{lot_id}", response_model=PartLotDetailRead)
async def get_lot_detail(
    lot_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> PartLotDetailRead:
    lot = await service.get_lot(lot_id)
    await _ensure_access(current_user, lot.filial_id, service.db)
    return await service.get_lot_detail(lot)


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


@router.get("/almacen/service-order-requests", response_model=list[ServiceOrderPartRequestRead])
async def list_service_order_requests(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> list[ServiceOrderPartRequestRead]:
    """Dispatched parts requests from Órdenes de Servicio, surfaced here so
    almacén staff can see them alongside warehouse-to-warehouse transfers."""
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_service_order_requests(filial_id)


@router.post("/almacen/service-order-requests/{transfer_id}/acknowledge", status_code=status.HTTP_204_NO_CONTENT)
async def acknowledge_service_order_request(
    transfer_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> None:
    await _ensure_access(current_user, filial_id, service.db)
    await service.acknowledge_service_order_request(transfer_id)


@router.post("/almacen/service-order-requests/{transfer_id}/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_service_order_request(
    transfer_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> None:
    """Almacén confirms the parts were physically handed over to the
    técnico — pauses the elapsed-time counter running since 'Pedido'."""
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    await service.complete_service_order_request(transfer_id, current_user.user_id)


@router.get("/almacen/part-sale-requests", response_model=list[PartSaleRequestRead])
async def list_part_sale_requests(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> list[PartSaleRequestRead]:
    """Counter parts sales (Venta de Repuestos), surfaced here so almacén
    staff can see them alongside Órdenes de Servicio requests and tell
    apart which destination (taller vs. mostrador) each one is headed to."""
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_part_sale_requests(filial_id)


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


# Motivos de Entrada (Ajustes → Motivos de Entrada) — holding-wide.


@router.get("/stock-in-reasons", response_model=list[StockInReasonRead])
async def list_stock_in_reasons(
    filial_id: uuid.UUID = Query(...),
    include_inactive: bool = Query(default=False),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> list[StockInReasonRead]:
    await _ensure_ajustes_access(current_user, filial_id, service.db)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.list_stock_in_reasons(holding_id, include_inactive)


@router.post("/stock-in-reasons", response_model=StockInReasonRead, status_code=status.HTTP_201_CREATED)
async def create_stock_in_reason(
    payload: StockInReasonCreate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> StockInReasonRead:
    await _ensure_ajustes_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.create_stock_in_reason(holding_id, payload)


@router.patch("/stock-in-reasons/{reason_id}", response_model=StockInReasonRead)
async def update_stock_in_reason(
    reason_id: uuid.UUID,
    payload: StockInReasonUpdate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> StockInReasonRead:
    await _ensure_ajustes_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.update_stock_in_reason(reason_id, holding_id, payload)


@router.post("/stock-in-reasons/{reason_id}/activate", response_model=StockInReasonRead)
async def activate_stock_in_reason(
    reason_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> StockInReasonRead:
    await _ensure_ajustes_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.set_stock_in_reason_active(reason_id, holding_id, is_active=True)


@router.post("/stock-in-reasons/{reason_id}/deactivate", response_model=StockInReasonRead)
async def deactivate_stock_in_reason(
    reason_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: AlmacenService = Depends(get_service),
) -> StockInReasonRead:
    await _ensure_ajustes_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.set_stock_in_reason_active(reason_id, holding_id, is_active=False)
