import asyncio
import hashlib
import hmac
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import BadRequestError, NotFoundError
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.concesionario.opportunities import OpportunityService, read_opportunity
from app.modules.concesionario.opportunity_router import access
from app.modules.concesionario.sales_workflow import CLOSING_FILES, DOCUMENTS, SalesWorkflow
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access

router = APIRouter(tags=["Vehicle sales workflow"])


class Versioned(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=1)


class FinancingSubmission(Versioned):
    provider: str = Field(min_length=2, max_length=50)
    external_reference: str = Field(min_length=3, max_length=100)


class FinancingDecision(FinancingSubmission):
    approved: bool
    reference: str = Field(min_length=3, max_length=500)


class FinancingClosing(Versioned):
    insurer: str = Field(min_length=2, max_length=150)
    policy_number: str = Field(min_length=2, max_length=100)


class CancelReservation(Versioned):
    account_id: uuid.UUID | None = None
    reference: str = Field(min_length=3, max_length=60)
    reason: str = Field(min_length=3, max_length=500)


class DeliveryChecks(Versioned):
    documentos_entregados: bool
    llaves_entregadas: bool
    funciones_explicadas: bool
    invitacion_app_enviada: bool


class HookDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    opportunity_id: uuid.UUID
    filial_id: uuid.UUID
    event_id: uuid.UUID
    provider: str = Field(min_length=2, max_length=50)
    external_reference: str = Field(min_length=3, max_length=100)
    approved: bool
    reference: str = Field(min_length=3, max_length=500)


async def editable(db, user, key, version):
    opp = await OpportunityService(db).get(key)
    await access(db, user, opp.filial_id, True)
    return await SalesWorkflow(db).locked(key, version)


async def finish(db, opp):
    await db.commit()
    return read_opportunity(await OpportunityService(db).get(opp.id))


@router.get("/vehicle-opportunities/accounts/available")
async def accounts(
    filial_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await access(db, user, filial_id)
    from app.modules.administracion.models import Account

    rows = (
        await db.execute(
            select(Account)
            .where(Account.filial_id == filial_id, Account.is_active.is_(True))
            .order_by(Account.name)
        )
    ).scalars()
    return [{"id": r.id, "name": r.name, "currency": r.currency.value} for r in rows]


@router.post("/vehicle-opportunities/{key}/documents/{kind}")
async def upload(
    key: uuid.UUID,
    kind: str,
    expected_version: int = Form(..., ge=1),
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if kind not in DOCUMENTS and kind not in CLOSING_FILES:
        raise BadRequestError("Tipo de documento inválido.")
    opp = await editable(db, user, key, expected_version)
    from app.core.storage import save_private_attachment

    reference = await save_private_attachment(file, category="credit-documents")
    await SalesWorkflow(db).file(
        opp, kind, reference, Path(file.filename or "Documento").name, user
    )
    return await finish(db, opp)


@router.get("/vehicle-opportunities/{key}/documents/{kind}")
async def download(
    key: uuid.UUID,
    kind: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    opp = await OpportunityService(db).get(key)
    await access(db, user, opp.filial_id)
    info = (opp.workflow or {}).get("files", {}).get(kind)
    if not info:
        raise NotFoundError("Documento no registrado.")
    from app.core.storage import _s3_client, private_pickup_location

    storage, bucket, location = private_pickup_location(info["reference"])
    if storage == "s3":
        url = await asyncio.to_thread(
            _s3_client().generate_presigned_url,
            "get_object",
            Params={"Bucket": bucket, "Key": location},
            ExpiresIn=300,
        )
        return RedirectResponse(url, headers={"Cache-Control": "no-store"})
    if not Path(location).is_file():
        raise NotFoundError("Documento no disponible.")
    return FileResponse(
        location, filename=info.get("filename", "Documento"), headers={"Cache-Control": "no-store"}
    )


@router.get("/vehicle-opportunities/{key}/proforma/pdf")
async def proforma(
    key: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    opp = await OpportunityService(db).get(key)
    await access(db, user, opp.filial_id)
    if opp.quoted_amount is None:
        raise BadRequestError("Cotiza el vehículo antes de generar la proforma.")
    from io import BytesIO

    from reportlab.pdfgen.canvas import Canvas

    buffer = BytesIO()
    pdf = Canvas(buffer)
    pdf.setTitle("Cotización proforma")
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(50, 790, "MOTRIX · COTIZACIÓN PROFORMA")
    pdf.setFont("Helvetica", 10)
    amount_text = f"{opp.quoted_amount:,.2f}".translate(str.maketrans({",": ".", ".": ","}))
    lines = [
        "No sustituye una factura fiscal ni acredita pagos recibidos.",
        f"Cliente: {opp.client_name}",
        f"Vehículo: {opp.vehicle_label}",
        f"VIN: {opp.vehicle.vin if opp.vehicle else ''}",
        f"Precio cotizado: {opp.quoted_currency} {amount_text}",
        f"Descuento: {opp.discount_percentage or 0}%",
        f"Válida hasta: {opp.quote_expires_at or 'Vigencia original'}",
        "IGTF según la forma de pago al facturar.",
    ]
    for i, line in enumerate(lines):
        pdf.drawString(50, 750 - i * 26, str(line)[:140])
    pdf.save()
    return Response(
        buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="proforma.pdf"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/vehicle-opportunities/{key}/financing/submit")
async def submit(
    key: uuid.UUID,
    payload: FinancingSubmission,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    opp = await editable(db, user, key, payload.expected_version)
    await SalesWorkflow(db).submit_financing(
        opp, payload.provider, payload.external_reference, user
    )
    return await finish(db, opp)


@router.post("/vehicle-opportunities/{key}/financing/decision")
async def decision(
    key: uuid.UUID,
    payload: FinancingDecision,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    snapshot = await OpportunityService(db).get(key)
    await ensure_module_access(db, user, snapshot.filial_id, "administracion", AccessLevel.EDITAR)
    opp = await editable(db, user, key, payload.expected_version)
    await SalesWorkflow(db).decision(
        opp,
        payload.approved,
        payload.reference,
        payload.provider,
        payload.external_reference,
        user.user_id,
    )
    return await finish(db, opp)


@router.post("/vehicle-opportunities/{key}/financing/closing")
async def closing(
    key: uuid.UUID,
    payload: FinancingClosing,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    opp = await editable(db, user, key, payload.expected_version)
    await SalesWorkflow(db).closing(opp, payload.insurer, payload.policy_number, user)
    return await finish(db, opp)


@router.post("/vehicle-opportunities/{key}/reservation/cancel")
async def cancel(
    key: uuid.UUID,
    payload: CancelReservation,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    snapshot = await OpportunityService(db).get(key)
    await ensure_module_access(db, user, snapshot.filial_id, "administracion", AccessLevel.EDITAR)
    opp = await editable(db, user, key, payload.expected_version)
    await SalesWorkflow(db).cancel_reservation(opp, payload, user)
    return await finish(db, opp)


@router.put("/vehicle-opportunities/{key}/delivery/checklist")
async def checklist(
    key: uuid.UUID,
    payload: DeliveryChecks,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    opp = await editable(db, user, key, payload.expected_version)
    await SalesWorkflow(db).delivery_checks(
        opp, payload.model_dump(exclude={"expected_version"}), user
    )
    return await finish(db, opp)


@router.post("/integrations/vehicle-financing/decision")
async def webhook(
    request: Request,
    x_motrix_timestamp: str = Header(""),
    x_motrix_signature: str = Header(""),
    db: AsyncSession = Depends(get_db),
):
    secret = get_settings().vehicle_financing_webhook_secret
    if not secret:
        raise HTTPException(503, "La integración de financiamiento no está configurada.")
    try:
        timestamp = int(x_motrix_timestamp)
    except ValueError:
        raise HTTPException(401, "Firma inválida.")
    if abs(time.time() - timestamp) > 300:
        raise HTTPException(401, "Firma vencida.")
    body = await request.body()
    if len(body) > 8192:
        raise HTTPException(413, "Solicitud demasiado grande.")
    signature = hmac.new(
        secret.encode(), x_motrix_timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, x_motrix_signature):
        raise HTTPException(401, "Firma inválida.")
    try:
        payload = HookDecision.model_validate_json(body)
    except ValueError:
        raise HTTPException(422, "Datos de decisión inválidos.")
    opp = await OpportunityService(db).get(payload.opportunity_id)
    if opp.filial_id != payload.filial_id:
        raise HTTPException(403, "La oportunidad no pertenece a la sucursal.")
    from app.modules.filiales.models import Filial
    from app.modules.holdings.models import Holding

    filial = await db.get(Filial, opp.filial_id)
    holding = await db.get(Holding, filial.holding_id) if filial else None
    if not filial or not filial.is_active or not holding or not holding.is_active:
        raise HTTPException(403, "La organización está suspendida.")
    opp = await SalesWorkflow(db).locked(opp.id, opp.version)
    if str(payload.event_id) not in (opp.workflow or {}).get("decision_events", []):
        await SalesWorkflow(db).decision(
            opp,
            payload.approved,
            payload.reference,
            payload.provider,
            payload.external_reference,
            event_id=str(payload.event_id),
        )
    return await finish(db, opp)
