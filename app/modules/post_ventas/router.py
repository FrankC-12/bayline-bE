import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.post_ventas.schemas import (
    MaintenancePlanCreate,
    MaintenancePlanRead,
    MaintenancePlanUpdate,
    TemparioCreate,
    TemparioRead,
    TemparioUpdate,
    VehicleWarrantyBulkCreate,
    VehicleWarrantyBulkResult,
    VehicleWarrantyCreate,
    VehicleWarrantyRead,
    WarrantyPolicyCreate,
    WarrantyPolicyRead,
    WarrantyPolicyUpdate,
    WorkshopWarrantyRead,
)
from app.modules.post_ventas.service import PostVentasService
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access

MODULE_ID = "post-ventas"

router = APIRouter(tags=["Post Ventas"])


def get_service(db: AsyncSession = Depends(get_db)) -> PostVentasService:
    return PostVentasService(db)


async def _ensure_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, MODULE_ID, level)


@router.get("/temparios", response_model=list[TemparioRead])
async def list_temparios(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> list[TemparioRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_temparios(filial_id, search)


@router.get("/temparios/{tempario_id}", response_model=TemparioRead)
async def get_tempario(
    tempario_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> TemparioRead:
    tempario = await service.get_tempario(tempario_id)
    await _ensure_access(current_user, tempario.filial_id, service.db)
    return tempario


@router.post("/temparios", response_model=TemparioRead, status_code=status.HTTP_201_CREATED)
async def create_tempario(
    payload: TemparioCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> TemparioRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_tempario(payload)


@router.patch("/temparios/{tempario_id}", response_model=TemparioRead)
async def update_tempario(
    tempario_id: uuid.UUID,
    payload: TemparioUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> TemparioRead:
    existing = await service.get_tempario(tempario_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_tempario(tempario_id, payload)


@router.get("/maintenance-plans", response_model=list[MaintenancePlanRead])
async def list_maintenance_plans(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> list[MaintenancePlanRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_plans(filial_id, search)


@router.get("/maintenance-plans/{plan_id}", response_model=MaintenancePlanRead)
async def get_maintenance_plan(
    plan_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> MaintenancePlanRead:
    plan = await service.get_plan(plan_id)
    await _ensure_access(current_user, plan.filial_id, service.db)
    return plan


@router.post("/maintenance-plans", response_model=MaintenancePlanRead, status_code=status.HTTP_201_CREATED)
async def create_maintenance_plan(
    payload: MaintenancePlanCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> MaintenancePlanRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_plan(payload)


@router.patch("/maintenance-plans/{plan_id}", response_model=MaintenancePlanRead)
async def update_maintenance_plan(
    plan_id: uuid.UUID,
    payload: MaintenancePlanUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> MaintenancePlanRead:
    existing = await service.get_plan(plan_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_plan(plan_id, payload)


# Vehicle warranties (garantía de fábrica)


@router.get("/vehicle-warranties", response_model=list[VehicleWarrantyRead])
async def list_vehicle_warranties(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> list[VehicleWarrantyRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_vehicle_warranties(filial_id, search)


@router.get("/vehicle-warranties/by-vin/{vin}", response_model=VehicleWarrantyRead)
async def get_vehicle_warranty_by_vin(
    vin: str,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> VehicleWarrantyRead:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.get_vehicle_warranty_by_vin(filial_id, vin)


@router.post(
    "/vehicle-warranties", response_model=VehicleWarrantyRead, status_code=status.HTTP_201_CREATED
)
async def create_vehicle_warranty(
    payload: VehicleWarrantyCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> VehicleWarrantyRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_vehicle_warranty(payload, current_user.user_id)


@router.post(
    "/vehicle-warranties/bulk",
    response_model=VehicleWarrantyBulkResult,
    status_code=status.HTTP_201_CREATED,
)
async def bulk_create_vehicle_warranties(
    payload: VehicleWarrantyBulkCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> VehicleWarrantyBulkResult:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    created, skipped = await service.bulk_create_vehicle_warranties(
        payload.filial_id, payload.items, current_user.user_id
    )
    return VehicleWarrantyBulkResult(
        created=[service.warranty_to_read(w) for w in created], skipped=skipped
    )


# Workshop warranties (garantía de taller) — created automatically when an
# order is invoiced (BillingService.issue), never by hand.


@router.get("/workshop-warranties/by-vin/{vin}", response_model=list[WorkshopWarrantyRead])
async def list_workshop_warranties_by_vin(
    vin: str,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> list[WorkshopWarrantyRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_workshop_warranties_by_vin(filial_id, vin)


# Warranty policies — catalog selected on an ODS (see service_orders'
# labor_warranty_policy_id/parts_warranty_policy_id), never hard-deleted.


@router.get("/warranty-policies", response_model=list[WarrantyPolicyRead])
async def list_warranty_policies(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    policy_status: str | None = Query(default=None),
    applies_to: str | None = Query(default=None),
    covered_by: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> list[WarrantyPolicyRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_warranty_policies(filial_id, search, policy_status, applies_to, covered_by)


@router.get("/warranty-policies/{policy_id}", response_model=WarrantyPolicyRead)
async def get_warranty_policy(
    policy_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> WarrantyPolicyRead:
    policy = await service.get_warranty_policy(policy_id)
    await _ensure_access(current_user, policy.filial_id, service.db)
    return policy


@router.post("/warranty-policies", response_model=WarrantyPolicyRead, status_code=status.HTTP_201_CREATED)
async def create_warranty_policy(
    payload: WarrantyPolicyCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> WarrantyPolicyRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_warranty_policy(payload)


@router.patch("/warranty-policies/{policy_id}", response_model=WarrantyPolicyRead)
async def update_warranty_policy(
    policy_id: uuid.UUID,
    payload: WarrantyPolicyUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> WarrantyPolicyRead:
    existing = await service.get_warranty_policy(policy_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_warranty_policy(policy_id, payload)