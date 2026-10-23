"""Business-wide settings (IVA, IGTF, tasa BCV, comisión, mano de obra).

These used to live under the post_ventas module, gated by the same
permission as the Temparios catalog. They're business-wide financial/
operational parameters, not part of the service catalog, so they get their
own module id — the persistence (LaborSettings) and business logic stay in
post_ventas.service, only the access-control boundary moves here.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.ajustes.bulk import (
    EXAMPLES,
    HEADERS,
    BulkInput,
    CatalogBulkService,
    CatalogKind,
    template_csv,
)
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.post_ventas.schemas import LaborSettingsRead, LaborSettingsUpdate, SettingsPatch
from app.modules.post_ventas.service import PostVentasService
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access

MODULE_ID = "ajustes"

router = APIRouter(tags=["Ajustes"])


@router.get("/settings/bulk/templates")
async def bulk_templates(
    filial_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _ensure_access(current_user, filial_id, db)
    from typing import get_args

    from app.modules.post_ventas.enums import TemparioCategory
    from app.modules.vehicle_catalog.schemas import VehicleBodyType

    return [
        {
            "kind": kind,
            "columns": HEADERS[kind],
            "example": EXAMPLES[kind],
            "options": {
                "activo": ["si", "no"],
                **(
                    {"tipo_vehiculo": list(get_args(VehicleBodyType))}
                    if kind == CatalogKind.MODELS
                    else {}
                ),
                **(
                    {
                        "categoria": [category.value for category in TemparioCategory],
                        "garantia": ["standard", "custom", "none"],
                    }
                    if kind == CatalogKind.SERVICES
                    else {}
                ),
            },
        }
        for kind in CatalogKind
    ]


@router.get("/settings/bulk/templates/{kind}")
async def download_bulk_template(
    kind: CatalogKind,
    filial_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _ensure_access(current_user, filial_id, db)
    return Response(
        template_csv(kind).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="plantilla-{kind}.csv"'},
    )


@router.post("/settings/bulk/{kind}/preview")
async def preview_catalog_bulk(
    kind: CatalogKind,
    filial_id: uuid.UUID,
    payload: BulkInput,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _ensure_access(current_user, filial_id, db, AccessLevel.EDITAR)
    return await CatalogBulkService(db).prepare(kind, filial_id, current_user.user_id, payload.rows)


@router.post("/settings/bulk/{kind}/import")
async def import_catalog_bulk(
    kind: CatalogKind,
    filial_id: uuid.UUID,
    payload: BulkInput,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _ensure_access(current_user, filial_id, db, AccessLevel.EDITAR)
    return await CatalogBulkService(db).prepare(
        kind,
        filial_id,
        current_user.user_id,
        payload.rows,
        commit=True,
        review_hash=payload.review_hash,
    )


def get_service(db: AsyncSession = Depends(get_db)) -> PostVentasService:
    return PostVentasService(db)


async def _ensure_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, MODULE_ID, level)


@router.get("/settings", response_model=LaborSettingsRead)
@router.get("/labor-settings", response_model=LaborSettingsRead)
async def get_labor_settings(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> LaborSettingsRead:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.get_labor_settings(filial_id)


@router.patch("/labor-settings", response_model=LaborSettingsRead)
async def update_labor_settings(
    payload: LaborSettingsUpdate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> LaborSettingsRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_labor_settings(filial_id, payload)


@router.patch("/settings", response_model=LaborSettingsRead)
async def patch_settings(
    payload: "SettingsPatch",
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> LaborSettingsRead:
    """Guarda solo los valores de la sección editada y su usuario/fecha."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.modules.filiales.models import Filial
    from app.modules.post_ventas.models import LaborSettings
    from app.modules.users.models import User

    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    await service.db.execute(select(Filial.id).where(Filial.id == filial_id).with_for_update())
    await service.get_labor_settings(filial_id, commit=False)
    settings = (
        await service.db.execute(
            select(LaborSettings)
            .where(LaborSettings.filial_id == filial_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    from app.core.exceptions import BadRequestError

    warranty_days = (
        payload.workshop_warranty_days
        if "workshop_warranty_days" in payload.model_fields_set
        else settings.workshop_warranty_days
    )
    warranty_km = (
        payload.workshop_warranty_km
        if "workshop_warranty_km" in payload.model_fields_set
        else settings.workshop_warranty_km
    )
    if {"workshop_warranty_days", "workshop_warranty_km"}.intersection(
        payload.model_fields_set
    ) and not (warranty_days > 0 or warranty_km > 0):
        raise BadRequestError("La garantía estándar necesita días o kilómetros de cobertura.")
    user = await service.db.get(User, current_user.user_id)
    changes = dict(settings.field_changes or {})
    values = payload.model_dump(exclude_unset=True)
    for field, value in values.items():
        setattr(settings, field, value)
        changes[field] = dict(
            user_id=str(current_user.user_id),
            user_name=user.full_name if user else None,
            changed_at=datetime.now(UTC).isoformat(),
        )
    if "bcv_rate" in values:
        settings.bcv_rate_mode = "manual"
        from app.modules.service_orders.billing import billing_day

        settings.bcv_rate_date = billing_day()
    for source, target in [
        ("workshop_warranty_days", "workshop_parts_warranty_days"),
        ("workshop_warranty_km", "workshop_parts_warranty_km"),
    ]:
        if source in values:
            setattr(settings, target, values[source])
    settings.field_changes = changes
    await service.db.commit()
    await service.db.refresh(settings)
    return settings


@router.post("/settings/bcv/restore", response_model=LaborSettingsRead)
async def restore_bcv(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> LaborSettingsRead:
    """Restablece la última tasa oficial disponible sin usar el valor manual."""
    from sqlalchemy import select

    from app.core.exceptions import ConflictError
    from app.modules.exchange_rates.models import ExchangeRate
    from app.modules.filiales.models import Filial

    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    await service.db.execute(select(Filial.id).where(Filial.id == filial_id).with_for_update())
    rate = (
        await service.db.execute(
            select(ExchangeRate)
            .where(ExchangeRate.currency == "USD")
            .order_by(ExchangeRate.value_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if rate is None:
        raise ConflictError("No hay una tasa oficial disponible para restaurar.")
    settings = await service.get_labor_settings(filial_id, commit=False)
    settings.bcv_rate_mode = "automatic"
    settings.bcv_rate = rate.rate_ves
    settings.bcv_rate_date = rate.value_date
    from datetime import UTC, datetime

    from app.modules.users.models import User

    user = await service.db.get(User, current_user.user_id)
    changes = dict(settings.field_changes or {})
    changes["bcv_rate"] = dict(
        user_id=str(current_user.user_id),
        user_name=user.full_name if user else None,
        changed_at=datetime.now(UTC).isoformat(),
    )
    settings.field_changes = changes
    await service.db.commit()
    await service.db.refresh(settings)
    return settings
