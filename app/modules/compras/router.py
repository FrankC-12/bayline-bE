import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.compras.schemas import (
    ReceptionCreate,
    VehiclePurchaseOrderCreate,
    VehiclePurchaseOrderDetailRead,
    VehiclePurchaseOrderInvoiceCreate,
    VehiclePurchaseOrderRead,
)
from app.modules.compras.service import ComprasService
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access

MODULE_ID = "compras"
# Registering the supplier's invoice is explicitly an Administración action
# (see backlog: "Administración registra la factura del proveedor vinculada
# a la OC") — a different permission boundary from creating the OC and
# recording receptions, which stay gated on "compras".
ADMINISTRACION_MODULE_ID = "administracion"

router = APIRouter(prefix="/compras", tags=["Compras"])


def get_service(db: AsyncSession = Depends(get_db)) -> ComprasService:
    return ComprasService(db)


async def _ensure_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, MODULE_ID, level)


async def _ensure_administracion_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, ADMINISTRACION_MODULE_ID, level)


@router.get("/vehicle-orders", response_model=list[VehiclePurchaseOrderRead])
async def list_vehicle_purchase_orders(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: ComprasService = Depends(get_service),
) -> list[VehiclePurchaseOrderRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_vehicle_purchase_orders(filial_id)


@router.post("/vehicle-orders", response_model=VehiclePurchaseOrderRead, status_code=status.HTTP_201_CREATED)
async def create_vehicle_purchase_order(
    payload: VehiclePurchaseOrderCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ComprasService = Depends(get_service),
) -> VehiclePurchaseOrderRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_vehicle_purchase_order(payload, current_user.user_id)


@router.get("/vehicle-orders/{order_id}", response_model=VehiclePurchaseOrderDetailRead)
async def get_vehicle_purchase_order(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ComprasService = Depends(get_service),
) -> VehiclePurchaseOrderDetailRead:
    filial_id = await service.get_order_filial(order_id)
    await _ensure_access(current_user, filial_id, service.db)
    return await service.get_vehicle_purchase_order(order_id)


@router.post("/vehicle-orders/{order_id}/receptions", response_model=VehiclePurchaseOrderDetailRead)
async def add_reception(
    order_id: uuid.UUID,
    payload: ReceptionCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ComprasService = Depends(get_service),
) -> VehiclePurchaseOrderDetailRead:
    filial_id = await service.get_order_filial(order_id)
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    return await service.add_reception(order_id, payload, current_user.user_id)


@router.post("/vehicle-orders/{order_id}/invoices", response_model=VehiclePurchaseOrderDetailRead)
async def add_invoice(
    order_id: uuid.UUID,
    payload: VehiclePurchaseOrderInvoiceCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ComprasService = Depends(get_service),
) -> VehiclePurchaseOrderDetailRead:
    filial_id = await service.get_order_filial(order_id)
    await _ensure_administracion_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    return await service.add_invoice(order_id, payload, current_user.user_id)


@router.post("/vehicle-orders/{order_id}/cancel", response_model=VehiclePurchaseOrderRead)
async def cancel_vehicle_purchase_order(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ComprasService = Depends(get_service),
) -> VehiclePurchaseOrderRead:
    filial_id = await service.get_order_filial(order_id)
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    return await service.cancel_vehicle_purchase_order(order_id)
