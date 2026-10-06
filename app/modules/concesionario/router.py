import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.storage import save_upload_image
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.concesionario.schemas import (
    VehicleCreate,
    VehiclePhotoRemoveInput,
    VehicleRead,
    VehicleReservationInput,
    VehicleSaleRead,
    VehicleStatusEventRead,
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


@router.post(
    "/dealership-vehicles", response_model=VehicleRead, status_code=status.HTTP_201_CREATED
)
async def create_vehicle(
    payload: VehicleCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ConcesionarioService = Depends(get_service),
) -> VehicleRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_vehicle(payload, current_user)


@router.post("/dealership-vehicles/{vehicle_id}/photos", response_model=VehicleRead)
async def upload_vehicle_photos(
    vehicle_id: uuid.UUID,
    photos: list[UploadFile] = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: ConcesionarioService = Depends(get_service),
) -> VehicleRead:
    existing = await service.get_vehicle(vehicle_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)

    settings = get_settings()
    photo_urls = [
        await save_upload_image(
            photo,
            directory=Path(settings.uploads_dir),
            subdir="dealership-vehicles",
            url_prefix=f"{settings.api_v1_prefix}/uploads",
            max_mb=settings.max_upload_mb,
        )
        for photo in photos
    ]
    return await service.add_vehicle_photos(vehicle_id, photo_urls)


@router.delete("/dealership-vehicles/{vehicle_id}/photos", response_model=VehicleRead)
async def remove_vehicle_photo(
    vehicle_id: uuid.UUID,
    payload: VehiclePhotoRemoveInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: ConcesionarioService = Depends(get_service),
) -> VehicleRead:
    existing = await service.get_vehicle(vehicle_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.remove_vehicle_photo(vehicle_id, payload.photo_url)


@router.patch("/dealership-vehicles/{vehicle_id}", response_model=VehicleRead)
async def update_vehicle(
    vehicle_id: uuid.UUID,
    payload: VehicleUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ConcesionarioService = Depends(get_service),
) -> VehicleRead:
    existing = await service.get_vehicle(vehicle_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_vehicle(vehicle_id, payload, current_user)


@router.post("/dealership-vehicles/{vehicle_id}/reserve", response_model=VehicleRead)
async def reserve_vehicle(
    vehicle_id: uuid.UUID,
    payload: VehicleReservationInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: ConcesionarioService = Depends(get_service),
) -> VehicleRead:
    existing = await service.get_vehicle(vehicle_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.reserve_vehicle(vehicle_id, payload, current_user)


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


@router.get(
    "/dealership-vehicles/{vehicle_id}/status-history", response_model=list[VehicleStatusEventRead]
)
async def vehicle_status_history(
    vehicle_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ConcesionarioService = Depends(get_service),
):
    vehicle = await service.get_vehicle(vehicle_id)
    await _ensure_access(current_user, vehicle.filial_id, service.db)
    return await service.list_status_history(vehicle_id)


@router.get("/vehicle-sales/{sale_id}/document")
async def vehicle_sale_document(
    sale_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ConcesionarioService = Depends(get_service),
):
    sale = await service.get_sale(sale_id)
    await _ensure_access(current_user, sale.filial_id, service.db)
    return await service.sale_document(sale)


@router.get("/vehicle-sales/{sale_id}/invoice/pdf")
async def vehicle_sale_invoice_pdf(
    sale_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: ConcesionarioService = Depends(get_service),
):
    from fastapi.responses import Response
    from app.core.documents.invoice_pdf import render_invoice_pdf
    from app.modules.filiales.models import Filial

    sale = await service.get_sale(sale_id)
    await _ensure_access(current_user, sale.filial_id, service.db)
    vehicle = await service.get_vehicle(sale.vehicle_id)
    filial = await service.db.get(Filial, sale.filial_id)
    mileage = (
        f"{sale.mileage_at_sale:,} km" if sale.mileage_at_sale is not None else "No registrado"
    )
    data = {
        "issuer": filial.name,
        "code": "FAC-" + sale.code,
        "issued_at": sale.created_at.isoformat(),
        "currency": "Bs." if vehicle.price_currency == "VES" else "USD",
        "client": sale.client_name,
        "client_document": sale.client_document or "",
        "vehicle": f"{vehicle.brand} {vehicle.model} · {vehicle.year}\nVIN: {vehicle.vin or '—'} · Placa: {vehicle.plate or 'Sin placa'}\nKilometraje al vender: {mileage}",
        "lines": [
            {
                "description": f"{vehicle.brand} {vehicle.model} · {sale.sale_type.value}",
                "quantity": 1,
                "total": float(sale.final_price) - float(sale.igtf_amount),
            }
        ],
        "totals": [
            (
                "Precio de venta antes de IGTF",
                float(sale.final_price) - float(sale.igtf_amount),
            ),
            ("IGTF", sale.igtf_amount),
            ("Total", sale.final_price),
        ],
        "payments": ["Modalidad: " + sale.sale_type.value],
        "pending": 0 if sale.sale_type.value == "contado" else sale.final_price,
        "notes": ["Precio y kilometraje registrados al vender."],
    }
    return Response(
        render_invoice_pdf(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="factura-{sale.id}.pdf"'},
    )
