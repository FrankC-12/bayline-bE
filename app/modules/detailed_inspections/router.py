import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import BadRequestError
from app.core.storage import ALLOWED_IMAGE_TYPES, save_upload_attachment, save_upload_image
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.detailed_inspections.schemas import DetailedInspectionRead, InspectionData
from app.modules.detailed_inspections.service import DetailedInspectionService
from app.modules.roles.enums import AccessLevel
from app.modules.service_orders.access import require_order_access
from app.modules.service_orders.guards import require_editable_order
from app.modules.service_orders.service import ServiceOrderService

UserDependency = Annotated[CurrentUser, Depends(get_current_user)]
DatabaseDependency = Annotated[AsyncSession, Depends(get_db)]

router = APIRouter(prefix="/detailed-inspections", tags=["Inspecciones minuciosas"])


async def context(order_id, user, db, *, edit=False):
    # Lock the ODS first: simultaneous mobile patches merge against fresh data,
    # and only one inspection can be created for each order.
    order = (
        await require_editable_order(db, order_id)
        if edit
        else await ServiceOrderService(db).get_order(order_id)
    )
    await require_order_access(user, order, db, AccessLevel.EDITAR if edit else AccessLevel.VER)
    return order, DetailedInspectionService(db)


@router.get("/template", summary="Consultar estructura y opciones del formulario móvil")
async def template(user: UserDependency):
    return {
        "version": 3,
        "total_steps": 5,
        "sections": [
            {"key": "engine", "label": "Compartimiento del motor", "required": True},
            {"key": "underbody", "label": "Debajo del vehículo", "required": True},
            {"key": "tires", "label": "Presión de neumáticos", "required": True},
            {"key": "brakes", "label": "Frenos", "required": True},
            {"key": "fluids", "label": "Fluidos y niveles", "required": False},
            {"key": "interior", "label": "Interior del vehículo", "required": False},
            {"key": "annexes", "label": "Anexos", "required": False},
            {"key": "completion", "label": "Culminación del trabajo", "required": True},
        ],
        "schema": InspectionData.model_json_schema(),
    }


@router.get(
    "/by-order/{order_id}",
    response_model=DetailedInspectionRead | None,
    summary="Consultar inspección, progreso y anexos de la ODS",
)
async def read(
    order_id: uuid.UUID,
    user: UserDependency,
    db: DatabaseDependency,
):
    order, service = await context(order_id, user, db)
    return await service.read(await service.get(order_id), order)


@router.patch(
    "/by-order/{order_id}",
    response_model=DetailedInspectionRead,
    summary="Crear o guardar avances parciales de la inspección",
    description="Combina solamente los campos enviados; null limpia un campo. No completa la ODS.",
)
async def save(
    order_id: uuid.UUID,
    payload: InspectionData,
    user: UserDependency,
    db: DatabaseDependency,
):
    order, service = await context(order_id, user, db, edit=True)
    return await service.read(await service.save(order_id, payload, user.user_id), order)


@router.post(
    "/by-order/{order_id}/complete",
    response_model=DetailedInspectionRead,
    summary="Completar inspección minuciosa",
    description="Valida las cuatro revisiones; anexos y observaciones opcionales. Idempotente.",
)
async def complete(
    order_id: uuid.UUID,
    user: UserDependency,
    db: DatabaseDependency,
):
    order, service = await context(order_id, user, db, edit=True)
    return await service.read(await service.complete(order_id, user.user_id), order)


@router.post(
    "/by-order/{order_id}/annexes",
    response_model=DetailedInspectionRead,
    summary="Adjuntar PDF del diagnóstico del scanner",
    description="Anexo opcional, incluso al completar la inspección, si la ODS es editable.",
)
async def upload_annex(
    order_id: uuid.UUID,
    file: Annotated[UploadFile, File()],
    user: UserDependency,
    db: DatabaseDependency,
):
    order, service = await context(order_id, user, db, edit=True)
    if file.content_type != "application/pdf":
        raise BadRequestError(
            "El anexo del scanner debe ser un PDF.", error_code="unsupported_file_type"
        )
    if not (await file.read(5)).startswith(b"%PDF-"):
        raise BadRequestError("El archivo no contiene un PDF válido.", error_code="invalid_pdf")
    await file.seek(0)
    inspection = await service.draft(order_id, user.user_id)
    settings = get_settings()
    url = await save_upload_attachment(
        file,
        directory=Path(settings.uploads_dir),
        subdir="detailed-inspections",
        url_prefix=f"{settings.api_v1_prefix}/uploads",
        max_mb=settings.max_upload_mb,
    )
    inspection = await service.add_annex(
        inspection, Path(file.filename or "diagnostico.pdf").name[:255], url, user.user_id
    )
    return await service.read(inspection, order)


@router.post("/by-order/{order_id}/photos", response_model=DetailedInspectionRead)
async def upload_photos(
    order_id: uuid.UUID,
    photos: Annotated[list[UploadFile], File()],
    user: UserDependency,
    db: DatabaseDependency,
):
    order, service = await context(order_id, user, db, edit=True)
    if not photos or len(photos) > 20:
        raise BadRequestError("Adjunta entre 1 y 20 fotos por solicitud.")
    if any(photo.content_type not in ALLOWED_IMAGE_TYPES for photo in photos):
        raise BadRequestError("Solo se admiten fotos JPEG, PNG, WebP o HEIC.")
    inspection = await service.draft(order_id, user.user_id)
    settings = get_settings()
    for photo in photos:
        url = await save_upload_image(
            photo,
            directory=Path(settings.uploads_dir),
            subdir="detailed-inspections",
            url_prefix=f"{settings.api_v1_prefix}/uploads",
            max_mb=settings.max_upload_mb,
        )
        await service.add_annex(
            inspection,
            Path(photo.filename or "foto.jpg").name[:255],
            url,
            user.user_id,
            commit=False,
        )
    await db.commit()
    await db.refresh(inspection)
    return await service.read(inspection, order)


@router.delete(
    "/by-order/{order_id}/annexes/{annex_id}",
    response_model=DetailedInspectionRead,
    summary="Quitar un anexo de la inspección",
)
async def delete_annex(
    order_id: uuid.UUID,
    annex_id: uuid.UUID,
    user: UserDependency,
    db: DatabaseDependency,
):
    order, service = await context(order_id, user, db, edit=True)
    return await service.read(await service.delete_annex(order_id, annex_id, user.user_id), order)
