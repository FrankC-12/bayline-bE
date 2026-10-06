import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.storage import save_upload_image
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.filiales.exceptions import FilialNotFoundError
from app.modules.filiales.models import Filial
from app.modules.parts.enums import ReturnCondition, ReturnReason
from app.modules.parts.exceptions import MissingReturnPhotoError
from app.modules.parts.schemas import (
    PartBulkCreate,
    PartBulkResult,
    PartCategoryCreate,
    PartCategoryRead,
    PartCategoryUpdate,
    PartCreate,
    PartMeasureCreate,
    PartMeasureRead,
    PartMeasureUpdate,
    PartRead,
    PartReturnCreate,
    PartReturnRead,
    PartSaleCreate,
    PartSalePaymentCreate,
    PartSalePaymentQuote,
    PartSalePaymentQuoteInput,
    PartSaleQuoteInput,
    PartSaleQuoteRead,
    PartSaleRead,
    PartSaleUpdate,
    PartUpdate,
)
from app.modules.parts.service import PartsService
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access

MODULE_ID = "repuestos"

router = APIRouter(tags=["Parts"])


def get_service(db: AsyncSession = Depends(get_db)) -> PartsService:
    return PartsService(db)


async def _ensure_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, MODULE_ID, level)


COBRAR_MODULE_ID = "finanzas-cobrar"


async def _ensure_sale_payment_access(
    current_user: CurrentUser, filial_id: uuid.UUID, db: AsyncSession
) -> None:
    await ensure_module_access(db, current_user, filial_id, COBRAR_MODULE_ID, AccessLevel.EDITAR)


async def _holding_id_for_filial(db: AsyncSession, filial_id: uuid.UUID) -> uuid.UUID:
    filial = await db.get(Filial, filial_id)
    if filial is None:
        raise FilialNotFoundError(str(filial_id))
    return filial.holding_id


@router.get("/parts", response_model=list[PartRead])
async def list_parts(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> list[PartRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_parts(filial_id, search, include_inactive)


@router.post("/parts", response_model=PartRead, status_code=status.HTTP_201_CREATED)
async def create_part(
    payload: PartCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_part(payload)


@router.post("/parts/bulk", response_model=PartBulkResult, status_code=status.HTTP_201_CREATED)
async def bulk_create_parts(
    payload: PartBulkCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartBulkResult:
    """Create many parts at once (from a pasted list or an uploaded spreadsheet).
    Items whose code already exists are skipped, not rejected."""
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    created, skipped = await service.bulk_create_parts(payload.filial_id, payload.items)
    return PartBulkResult(created=created, skipped=skipped)


@router.patch("/parts/{part_id}", response_model=PartRead)
async def update_part(
    part_id: uuid.UUID,
    payload: PartUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartRead:
    existing = await service.get_part(part_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_part(part_id, payload)


@router.post("/parts/{part_id}/activate", response_model=PartRead)
async def activate_part(
    part_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartRead:
    existing = await service.get_part(part_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.set_part_active(part_id, is_active=True)


@router.post("/parts/{part_id}/deactivate", response_model=PartRead)
async def deactivate_part(
    part_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartRead:
    existing = await service.get_part(part_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.set_part_active(part_id, is_active=False)


# Part categories (Ajustes → Categorías de Repuestos) — holding-wide, same
# access boundary as the parts catalog itself.


@router.get("/part-categories", response_model=list[PartCategoryRead])
async def list_part_categories(
    filial_id: uuid.UUID = Query(...),
    include_inactive: bool = Query(default=False),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> list[PartCategoryRead]:
    await _ensure_access(current_user, filial_id, service.db)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.list_categories(holding_id, include_inactive)


@router.post("/part-categories", response_model=PartCategoryRead, status_code=status.HTTP_201_CREATED)
async def create_part_category(
    payload: PartCategoryCreate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartCategoryRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.create_category(holding_id, payload)


@router.patch("/part-categories/{category_id}", response_model=PartCategoryRead)
async def update_part_category(
    category_id: uuid.UUID,
    payload: PartCategoryUpdate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartCategoryRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.update_category(category_id, holding_id, payload)


@router.post("/part-categories/{category_id}/activate", response_model=PartCategoryRead)
async def activate_part_category(
    category_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartCategoryRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.set_category_active(category_id, holding_id, is_active=True)


@router.post("/part-categories/{category_id}/deactivate", response_model=PartCategoryRead)
async def deactivate_part_category(
    category_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartCategoryRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.set_category_active(category_id, holding_id, is_active=False)


# Part measures (Ajustes → Medidas de Repuestos) — holding-wide.


@router.get("/part-measures", response_model=list[PartMeasureRead])
async def list_part_measures(
    filial_id: uuid.UUID = Query(...),
    include_inactive: bool = Query(default=False),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> list[PartMeasureRead]:
    await _ensure_access(current_user, filial_id, service.db)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.list_measures(holding_id, include_inactive)


@router.post("/part-measures", response_model=PartMeasureRead, status_code=status.HTTP_201_CREATED)
async def create_part_measure(
    payload: PartMeasureCreate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartMeasureRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.create_measure(holding_id, payload)


@router.patch("/part-measures/{measure_id}", response_model=PartMeasureRead)
async def update_part_measure(
    measure_id: uuid.UUID,
    payload: PartMeasureUpdate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartMeasureRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.update_measure(measure_id, holding_id, payload)


@router.post("/part-measures/{measure_id}/activate", response_model=PartMeasureRead)
async def activate_part_measure(
    measure_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartMeasureRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.set_measure_active(measure_id, holding_id, is_active=True)


@router.post("/part-measures/{measure_id}/deactivate", response_model=PartMeasureRead)
async def deactivate_part_measure(
    measure_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartMeasureRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.set_measure_active(measure_id, holding_id, is_active=False)


@router.get("/part-sales", response_model=list[PartSaleRead])
async def list_part_sales(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> list[PartSaleRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_sales(filial_id, search)


@router.post("/part-sales/quote", response_model=PartSaleQuoteRead)
async def quote_part_sale(
    payload: PartSaleQuoteInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
):
    await _ensure_access(current_user, payload.filial_id, service.db)
    return await service.quote_sale(payload)


@router.get("/part-sales/{sale_id}", response_model=PartSaleRead)
async def get_part_sale(
    sale_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartSaleRead:
    sale = await service.get_sale(sale_id)
    await _ensure_access(current_user, sale.filial_id, service.db)
    return sale


@router.post("/part-sales", response_model=PartSaleRead, status_code=status.HTTP_201_CREATED)
async def create_part_sale(
    payload: PartSaleCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartSaleRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_sale(payload, current_user.user_id)


@router.patch("/part-sales/{sale_id}", response_model=PartSaleRead)
async def update_part_sale(
    sale_id: uuid.UUID,
    payload: PartSaleUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartSaleRead:
    existing = await service.get_sale(sale_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    if payload.status is None:
        return existing
    return await service.update_sale_status(
        sale_id, payload.status, payload.dispatched_lines, current_user.user_id
    )


@router.get("/part-sales/{sale_id}/billing")
async def get_part_sale_billing(
    sale_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
):
    sale = await service.get_sale(sale_id)
    await _ensure_sale_payment_access(current_user, sale.filial_id, service.db)
    context = await service.get_sale_billing_context(sale_id)
    return {
        "pending_amount": context["sale"].pending_amount,
        "bcv_rate": context["bcv_rate"],
        "bcv_date": context["bcv_date"],
        "accounts": context["accounts"],
    }


@router.post("/part-sales/{sale_id}/billing/quote", response_model=PartSalePaymentQuote)
async def quote_part_sale_payment(
    sale_id: uuid.UUID,
    payload: PartSalePaymentQuoteInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartSalePaymentQuote:
    sale = await service.get_sale(sale_id)
    await _ensure_sale_payment_access(current_user, sale.filial_id, service.db)
    return await service.quote_sale_payment(sale_id, payload)


@router.post("/part-sales/{sale_id}/payments", response_model=PartSaleRead)
async def collect_part_sale_payment(
    sale_id: uuid.UUID,
    payload: PartSalePaymentCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartSaleRead:
    sale = await service.get_sale(sale_id)
    await _ensure_sale_payment_access(current_user, sale.filial_id, service.db)
    return await service.collect_sale_payment(sale_id, payload, current_user.user_id)


@router.get("/part-returns", response_model=list[PartReturnRead])
async def list_part_returns(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> list[PartReturnRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_returns(filial_id)


@router.post("/part-returns", response_model=PartReturnRead, status_code=status.HTTP_201_CREATED)
async def create_part_return(
    filial_id: uuid.UUID = Form(...),
    part_id: uuid.UUID = Form(...),
    condition: ReturnCondition = Form(...),
    origin_warehouse: str = Form(...),
    destination_warehouse: str = Form(...),
    quantity: int = Form(...),
    reason: ReturnReason = Form(...),
    reason_notes: str | None = Form(None),
    photos: list[UploadFile] = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartReturnRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    if not photos:
        raise MissingReturnPhotoError()

    settings = get_settings()
    photo_urls = [
        await save_upload_image(
            photo,
            directory=Path(settings.uploads_dir),
            subdir="part-returns",
            url_prefix=f"{settings.api_v1_prefix}/uploads",
            max_mb=settings.max_upload_mb,
        )
        for photo in photos
    ]

    payload = PartReturnCreate(
        filial_id=filial_id,
        part_id=part_id,
        condition=condition,
        origin_warehouse=origin_warehouse,
        destination_warehouse=destination_warehouse,
        quantity=quantity,
        reason=reason,
        reason_notes=reason_notes,
        photo_urls=photo_urls,
    )
    return await service.create_return(payload, current_user.user_id)


@router.get("/part-sales/{sale_id}/invoice/pdf")
async def part_sale_invoice_pdf(
    sale_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
):
    from fastapi.responses import Response
    from app.core.documents.invoice_pdf import render_invoice_pdf
    from app.core.exceptions import BadRequestError

    sale = await service.get_sale(sale_id)
    await _ensure_access(current_user, sale.filial_id, service.db)
    if sale.status.value != "completado":
        raise BadRequestError("Confirma la entrega de mostrador antes de descargar la factura.")
    filial = await service.db.get(Filial, sale.filial_id)
    from app.modules.parts.models import Part

    descriptions = {}
    for line in sale.lines:
        part = await service.db.get(Part, line.part_id)
        descriptions[line.part_id] = f"{part.code} · {part.name}" if part else "Repuesto"
    from sqlalchemy import select
    from app.modules.administracion.models import Account, IncomeEntry
    from app.modules.administracion.enums import MovementSourceType

    payments = []
    entries = (
        (
            await service.db.execute(
                select(IncomeEntry)
                .where(
                    IncomeEntry.source_type == MovementSourceType.PART_SALE,
                    IncomeEntry.source_id == sale.id,
                )
                .order_by(IncomeEntry.entry_date, IncomeEntry.created_at)
            )
        )
        .scalars()
        .all()
    )
    for entry in entries:
        account = await service.db.get(Account, entry.account_id) if entry.account_id else None
        currency = entry.currency.value.upper()
        payments.append(
            f"{account.name if account else 'Cuenta registrada'} · {currency} {float(entry.amount):,.2f}"
        )
        if entry.exchange_rate:
            payments.append(f"Tasa de cobro: Bs. {float(entry.exchange_rate):,.8f} por USD")
    data = {
        "issuer": filial.name,
        "code": "FAC-" + sale.code,
        "issued_at": sale.created_at.isoformat(),
        "client": sale.client_name,
        "client_document": sale.client_document or "",
        "reference": "Venta " + sale.code,
        "lines": [
            {
                "description": descriptions[line.part_id],
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "total": line.line_total,
            }
            for line in sale.lines
        ],
        "totals": [
            ("Subtotal", sale.total),
            (f"IVA ({sale.iva_percentage}%)", sale.iva_amount),
            (f"IGTF ({sale.igtf_percentage}%)", sale.igtf_amount),
            ("Total", sale.total_with_taxes),
        ],
        "payments": payments + [f"Cobrado: USD {sale.amount_collected:,.2f}"],
        "pending": sale.pending_amount,
        "notes": ["Saldo y cobros a la fecha de descarga."],
    }
    return Response(
        render_invoice_pdf(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="factura-{sale.id}.pdf"'},
    )
