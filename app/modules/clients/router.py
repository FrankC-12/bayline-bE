import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.pagination import Page
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
    VehicleServiceHistoryRead,
)
from app.modules.clients.service import ClientService
from app.modules.post_ventas.schemas import VehicleWarrantyRead
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


async def _ensure_profile_access(current_user, filial_id, db, level=AccessLevel.VER):
    from app.modules.auth.exceptions import InsufficientPermissionsError

    try:
        await ensure_module_access(db, current_user, filial_id, "asesor-servicios", level)
    except InsufficientPermissionsError:
        await _ensure_access(current_user, filial_id, db, level)


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


@router.get("/page", response_model=Page[ClientRead], summary="Consultar clientes por páginas")
async def page_clients(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(None, max_length=150),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    service: ClientService = Depends(get_client_service),
):
    await _ensure_access(current_user, filial_id, service.db)
    return await service.page_clients(filial_id, search=search, page=page, page_size=page_size)


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


@router.get(
    "/vehicles/{vehicle_id}/mileage-history", response_model=list[VehicleMileageHistoryEntry]
)
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


@router.post(
    "/vehicles/{vehicle_id}/factory-warranty",
    tags=["Garantías"],
    status_code=201,
    response_model=VehicleWarrantyRead,
)
async def register_vehicle_factory_warranty(
    vehicle_id: uuid.UUID,
    starts_at: "date" = Form(...),
    duration_months: int | None = Form(None, ge=1),
    duration_km: int | None = Form(None, ge=1),
    note: str | None = Form(None, max_length=300),
    certificate: UploadFile | None = File(None),
    current_user: CurrentUser = Depends(get_current_user),
    service: ClientService = Depends(get_client_service),
):
    """Registra garantía externa desde la ficha del vehículo, con VIN precargado."""
    from sqlalchemy import select

    from app.core.exceptions import BadRequestError, ConflictError
    from app.core.storage import save_upload_image
    from app.modules.clients.models import Client, Vehicle
    from app.modules.concesionario.enums import VehicleStatus
    from app.modules.concesionario.models import DealershipVehicle
    from app.modules.post_ventas.models import VehicleWarranty
    from app.modules.post_ventas.schemas import VehicleWarrantyCreate
    from app.modules.post_ventas.service import PostVentasService

    vehicle = await service.db.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise BadRequestError("Vehículo no encontrado.")
    client = await service.db.get(Client, vehicle.client_id)
    await _ensure_profile_access(current_user, client.filial_id, service.db, AccessLevel.EDITAR)
    if not vehicle.vin:
        raise BadRequestError("Registra el VIN antes de crear la garantía.")
    if not ((duration_months or 0) > 0 or (duration_km or 0) > 0):
        raise BadRequestError("Indica meses o kilómetros de cobertura mayores a cero.")
    if (
        await service.db.execute(
            select(DealershipVehicle.id).where(
                DealershipVehicle.filial_id == client.filial_id,
                DealershipVehicle.vin == vehicle.vin,
                DealershipVehicle.status == VehicleStatus.VENDIDO,
            )
        )
    ).first():
        raise ConflictError(
            "La garantía de vehículos vendidos por nuestro concesionario se crea automáticamente."
        )
    from pydantic import ValidationError

    try:
        payload = VehicleWarrantyCreate(
            filial_id=client.filial_id,
            vin=vehicle.vin,
            brand=vehicle.brand,
            model=vehicle.model,
            starts_at=starts_at,
            duration_months=duration_months,
            duration_km=duration_km,
            note=note,
        )
    except ValidationError as exc:
        raise BadRequestError(
            "Revisa el VIN y los datos de cobertura registrados en el vehículo."
        ) from exc
    # Keep certificate and record creation in the same database transaction.
    from app.modules.filiales.models import Filial

    await service.db.execute(
        select(Filial.id).where(Filial.id == client.filial_id).with_for_update()
    )
    if (
        await service.db.execute(
            select(VehicleWarranty.id).where(
                VehicleWarranty.filial_id == client.filial_id, VehicleWarranty.vin == vehicle.vin
            )
        )
    ).first():
        raise ConflictError("Este VIN ya tiene una garantía registrada.")
    reader = PostVentasService(service.db)
    record = VehicleWarranty(
        filial_id=client.filial_id,
        vin=vehicle.vin,
        brand=vehicle.brand,
        model=vehicle.model,
        starts_at=payload.starts_at,
        duration_months=payload.duration_months,
        duration_km=payload.duration_km,
        expires_at=reader._expires_at(payload.starts_at, payload.duration_months),
        source="manual",
        note=payload.note,
        created_by_user_id=current_user.user_id,
    )
    if certificate:
        from pathlib import Path

        from app.core.config import get_settings

        config = get_settings()
        record.certificate_photo_url = await save_upload_image(
            certificate,
            directory=Path(config.uploads_dir),
            subdir="warranty-certificates",
            url_prefix="/uploads",
            max_mb=config.max_upload_mb,
        )
    service.db.add(record)
    await service.db.commit()
    await service.db.refresh(record)
    return reader.warranty_to_read(record, current_mileage=vehicle.mileage)


@router.get(
    "/vehicles/{vehicle_id}/service-history",
    tags=["Clientes"],
    response_model=VehicleServiceHistoryRead,
)
async def vehicle_service_history(
    vehicle_id: uuid.UUID,
    page: int = Query(1, ge=1),
    size: int = Query(30, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    service: ClientService = Depends(get_client_service),
):
    """Historial de ODS del vehículo/VIN en la misma filial, paginado."""
    from sqlalchemy import func, select

    from app.core.exceptions import NotFoundError
    from app.modules.clients.models import Client, Vehicle
    from app.modules.service_orders.models import ServiceOrder, ServiceOrderTask
    from app.modules.service_orders.service import ServiceOrderService

    vehicle = await service.db.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise NotFoundError("Vehículo no encontrado.")
    client = await service.db.get(Client, vehicle.client_id)
    await _ensure_profile_access(current_user, client.filial_id, service.db)
    statement = (
        select(ServiceOrder)
        .join(Vehicle, Vehicle.id == ServiceOrder.vehicle_id)
        .where(ServiceOrder.filial_id == client.filial_id)
    )
    statement = (
        statement.where(Vehicle.vin == vehicle.vin)
        if vehicle.vin
        else statement.where(Vehicle.id == vehicle_id)
    )
    count = (
        await service.db.execute(select(func.count()).select_from(statement.subquery()))
    ).scalar_one()
    orders = (
        (
            await service.db.execute(
                statement.order_by(ServiceOrder.created_at.desc(), ServiceOrder.id)
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    task_rows = (
        (
            await service.db.execute(
                select(ServiceOrderTask).where(
                    ServiceOrderTask.service_order_id.in_([order.id for order in orders])
                )
            )
        )
        .scalars()
        .all()
    )
    results = []
    for order in orders:
        summary = await ServiceOrderService(service.db).get_order_summary(order.id)
        results.append(
            dict(
                id=str(order.id),
                code=order.code,
                created_at=order.created_at,
                status=order.status,
                type=order.order_type.name,
                services=[
                    task.name_snapshot for task in task_rows if task.service_order_id == order.id
                ],
                mileage=(
                    order.exit_mileage if order.exit_mileage is not None else order.intake_mileage
                ),
                total=summary.total,
            )
        )
    return dict(items=results, total=count, page=page, size=size)
