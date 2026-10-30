"""Commercial tracking connected to actual stock reservations and vehicle sales."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ConflictError
from app.core.pagination import page_query
from app.core.search import fold_search
from app.core.venezuela_time import venezuela_today
from app.modules.clients.models import Client
from app.modules.concesionario.enums import VehicleStatus
from app.modules.concesionario.models import DealershipVehicle, VehicleSale
from app.modules.concesionario.opportunity_models import VehicleOpportunity as Opportunity

STAGES = (
    "prospecto",
    "cotizada",
    "reservada",
    "financiamiento",
    "facturada",
    "entregada",
    "perdida",
)


def event(opp, action, actor_id, note=None):
    opp.activity = [
        *(opp.activity or []),
        {
            "action": action,
            "actor_id": str(actor_id) if actor_id else None,
            "at": datetime.now(UTC).isoformat(),
            "note": note,
        },
    ]
    opp.version = (opp.version or 0) + 1


def has_reservation(opp):
    unit = opp.vehicle
    return bool(
        unit
        and unit.status == VehicleStatus.RESERVADO
        and unit.reserved_client_id == opp.client_id
        and unit.reservation_expires_at
        and unit.reservation_expires_at >= venezuela_today()
    )


def stage_sql():
    return case(
        (VehicleSale.delivered_at.is_not(None), "entregada"),
        (VehicleSale.id.is_not(None), "facturada"),
        (Opportunity.stage == "perdida", "perdida"),
        (Opportunity.stage == "financiamiento", "financiamiento"),
        (
            and_(
                DealershipVehicle.status == VehicleStatus.RESERVADO,
                DealershipVehicle.reserved_client_id == Opportunity.client_id,
                DealershipVehicle.reservation_expires_at >= venezuela_today(),
            ),
            "reservada",
        ),
        (Opportunity.quoted_at.is_not(None), "cotizada"),
        else_="prospecto",
    )


def effective_stage(opp):
    if opp.sale:
        return "entregada" if opp.sale.delivered_at else "facturada"
    if opp.stage == "perdida":
        return "perdida"
    if opp.stage == "financiamiento":
        return "financiamiento"
    if has_reservation(opp):
        return "reservada"
    return "cotizada" if opp.quoted_at else "prospecto"


def read_opportunity(opp):
    from app.modules.concesionario.sales_workflow import read_workflow

    unit, client, sale = opp.vehicle, opp.client, opp.sale
    return dict(
        workflow=read_workflow(opp),
        id=opp.id,
        code=f"OP-{opp.id.hex[:8].upper()}",
        filial_id=opp.filial_id,
        client_id=opp.client_id,
        client_name=client.full_name if client else opp.client_name,
        client_phone=client.phone_primary if client else None,
        client_document=(
            f"{client.document_type.value}-{client.document_number}" if client else None
        ),
        advisor_user_id=opp.advisor_user_id,
        advisor_name=opp.advisor.full_name if opp.advisor else None,
        origin=opp.origin,
        stage=effective_stage(opp),
        vehicle_id=opp.vehicle_id,
        vehicle_label=f"{unit.brand} {unit.model} {unit.year}" if unit else opp.vehicle_label,
        vehicle_status=unit.status.value if unit else None,
        sale_id=opp.sale_id,
        amount=(
            float(sale.final_price)
            if sale
            else float(opp.quoted_amount)
            if opp.quoted_amount is not None
            else None
        ),
        currency=((sale.vehicle_snapshot or {}).get("price_currency") if sale else None)
        or opp.quoted_currency,
        quoted_amount=float(opp.quoted_amount) if opp.quoted_amount is not None else None,
        quoted_at=opp.quoted_at,
        quote_expires_at=opp.quote_expires_at,
        discount_percentage=float(opp.discount_percentage or 0),
        sale_type=sale.sale_type.value if sale else opp.sale_type,
        notes=opp.notes,
        next_contact_at=opp.next_contact_at,
        lost_reason=opp.lost_reason,
        activity=opp.activity or [],
        version=opp.version,
        created_at=opp.created_at,
        delivered_at=sale.delivered_at if sale else None,
    )


class OpportunityService:
    def __init__(self, db):
        self.db = db

    def query(self):
        return select(Opportunity).options(
            selectinload(Opportunity.client),
            selectinload(Opportunity.vehicle),
            selectinload(Opportunity.advisor),
            selectinload(Opportunity.sale),
        )

    async def get(self, opportunity_id, lock=False):
        query = (
            self.query()
            .where(Opportunity.id == opportunity_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            query = query.with_for_update()
        opp = (await self.db.execute(query)).scalar_one_or_none()
        if not opp:
            raise BadRequestError("No se encontró la oportunidad.")
        return opp

    async def board(self, filial_id, search=None, advisor_id=None, page=1, page_size=100):
        base = (
            select(Opportunity)
            .outerjoin(Client, Client.id == Opportunity.client_id)
            .outerjoin(DealershipVehicle, DealershipVehicle.id == Opportunity.vehicle_id)
            .outerjoin(VehicleSale, VehicleSale.id == Opportunity.sale_id)
            .where(Opportunity.filial_id == filial_id)
        )
        if advisor_id:
            base = base.where(Opportunity.advisor_user_id == advisor_id)
        if search and search.strip():
            term = fold_search(search.strip())
            base = base.where(
                or_(
                    Client.normalized_name.contains(term, autoescape=True),
                    func.lower(Opportunity.client_name).contains(term, autoescape=True),
                    Client.document_number.contains(term, autoescape=True),
                    func.lower(Opportunity.vehicle_label).contains(term, autoescape=True),
                    func.lower(DealershipVehicle.sku).contains(term, autoescape=True),
                )
            )
        stage = stage_sql()
        counts = dict(
            (
                await self.db.execute(
                    base.with_only_columns(
                        stage, func.count(), maintain_column_froms=True
                    ).group_by(stage)
                )
            ).all()
        )
        query = base.options(
            selectinload(Opportunity.client),
            selectinload(Opportunity.vehicle),
            selectinload(Opportunity.advisor),
            selectinload(Opportunity.sale),
        ).order_by(Opportunity.updated_at.desc(), Opportunity.id)
        rows, meta = await page_query(self.db, query, page, page_size)
        return {
            **meta,
            "items": [read_opportunity(opp) for opp in rows.scalars()],
            "counts": {stage: counts.get(stage, 0) for stage in STAGES},
        }

    async def validate_advisor(self, filial_id, advisor_id):
        if advisor_id:
            from app.modules.concesionario.service import ConcesionarioService

            await ConcesionarioService(self.db)._validate_sale_advisor(filial_id, advisor_id)

    async def prospect(self, client, actor_id, advisor_id=None, origin=None, notes=None):
        await self.validate_advisor(client.filial_id, advisor_id)
        opp = Opportunity(
            filial_id=client.filial_id,
            client_id=client.id,
            client_name=client.full_name,
            advisor_user_id=advisor_id,
            origin=origin or client.lead_origin,
            notes=notes or client.lead_notes,
            vehicle_label=client.lead_vehicle_interest,
            next_contact_at=client.lead_next_contact_at,
            created_by_user_id=actor_id,
            activity=[],
            version=0,
        )
        event(opp, "creada", actor_id)
        self.db.add(opp)
        await self.db.flush()
        return opp

    async def create(self, payload, actor):
        client = await self.db.get(Client, payload.client_id)
        if not client or client.filial_id != payload.filial_id:
            raise BadRequestError("El cliente no pertenece a esta filial.")
        advisor = payload.advisor_user_id or (
            actor.user_id if actor.role_slug == "vendedor" else None
        )
        opp = await self.prospect(client, actor.user_id, advisor, payload.origin, payload.notes)
        await self.db.commit()
        return read_opportunity(await self.get(opp.id))

    def assert_version(self, opp, version):
        if opp.version != version:
            raise ConflictError(
                "La oportunidad cambió. Vuelve a abrirla para continuar.",
                error_code="opportunity_version_conflict",
            )

    async def update(self, opportunity_id, payload, actor):
        opp = await self.get(opportunity_id, True)
        self.assert_version(opp, payload.expected_version)
        if opp.sale_id:
            raise BadRequestError("La venta ya fue registrada. Consulta su detalle.")
        changes = payload.model_dump(exclude_unset=True)
        changes.pop("expected_version", None)
        stage = changes.pop("stage", None)
        deposit = (opp.workflow or {}).get("deposit", {})
        if stage == "perdida" and deposit.get("status") == "registrado":
            raise ConflictError(
                "Cancela la reserva con su devolución antes de cerrar esta oportunidad."
            )
        if stage == "prospecto" and deposit.get("status") == "devuelto":
            raise ConflictError(
                "La reserva ya fue liquidada. Crea una nueva oportunidad para otra negociación."
            )

        if stage == "perdida":
            if has_reservation(opp):
                raise BadRequestError("Libera primero la reserva de esta unidad desde el catálogo.")
            if not (payload.lost_reason or "").strip():
                raise BadRequestError("Indica por qué se perdió esta oportunidad.")
            opp.stage = "perdida"
        elif stage == "financiamiento":
            if not opp.quoted_at or opp.sale_type != "financiado":
                raise BadRequestError("Cotiza primero la unidad como financiada.")
            opp.stage = stage
        elif stage == "prospecto":
            if opp.stage != "perdida":
                raise BadRequestError("Solo se pueden reabrir oportunidades perdidas.")
            opp.stage = "prospecto"
            opp.lost_reason = None
        if "advisor_user_id" in changes:
            if (
                has_reservation(opp)
                and changes["advisor_user_id"] != opp.vehicle.reserved_by_user_id
            ):
                raise BadRequestError(
                    "La oportunidad debe conservar el vendedor responsable de la reserva."
                )
            await self.validate_advisor(opp.filial_id, changes["advisor_user_id"])
        for key, value in changes.items():
            setattr(opp, key, value)
        event(opp, "seguimiento", actor.user_id, stage)
        await self.db.commit()
        return read_opportunity(await self.get(opp.id))

    async def quote(self, opportunity_id, payload, actor):
        unit = (
            await self.db.execute(
                select(DealershipVehicle)
                .where(DealershipVehicle.id == payload.vehicle_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        opp = await self.get(opportunity_id, True)
        self.assert_version(opp, payload.expected_version)
        if (opp.workflow or {}).get("deposit", {}).get("status") == "registrado":
            raise ConflictError(
                "La cotización está vinculada a un abono. Cancela y liquida la reserva antes de cambiarla."
            )

        if opp.sale_id or opp.stage == "perdida":
            raise BadRequestError(
                "Reabre la oportunidad antes de cotizar; una venta registrada no se puede recotizar."
            )
        if not unit or unit.filial_id != opp.filial_id or unit.status == VehicleStatus.VENDIDO:
            raise BadRequestError("Selecciona una unidad disponible de esta filial.")
        if unit.status == VehicleStatus.RESERVADO and unit.reserved_client_id != opp.client_id:
            raise BadRequestError("Esta unidad está reservada para otro cliente.")
        if has_reservation(opp) and opp.vehicle_id != unit.id:
            raise BadRequestError("Libera la reserva antes de cambiar la unidad cotizada.")
        duplicate = (
            await self.db.execute(
                select(Opportunity.id)
                .where(
                    Opportunity.id != opp.id,
                    Opportunity.filial_id == opp.filial_id,
                    Opportunity.client_id == opp.client_id,
                    Opportunity.vehicle_id == unit.id,
                    Opportunity.sale_id.is_(None),
                    Opportunity.stage != "perdida",
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if duplicate:
            raise BadRequestError(
                "Ya hay otra oportunidad activa de este cliente para esta unidad."
            )
        amount = unit.price_financed if payload.sale_type == "financiado" else unit.cash_total
        if not amount or amount <= 0:
            raise BadRequestError("La unidad no tiene un precio válido configurado.")
        from app.modules.post_ventas.service import PostVentasService

        settings = await PostVentasService(self.db).get_labor_settings(opp.filial_id, commit=False)
        discount = Decimal(str(payload.discount_percentage))
        if discount > Decimal(str(settings.vehicle_discount_without_approval_percentage)):
            raise BadRequestError(
                "El descuento supera el límite autorizado. Solicita aprobación antes de cotizar."
            )
        amount = (Decimal(str(amount)) * (Decimal("1") - discount / 100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        opp.discount_percentage = discount
        opp.quote_expires_at = venezuela_today() + timedelta(days=settings.vehicle_quote_valid_days)
        opp.vehicle_id = unit.id
        opp.vehicle_label = f"{unit.brand} {unit.model} {unit.year}"
        opp.quoted_amount = amount
        opp.quoted_currency = unit.price_currency
        opp.sale_type = payload.sale_type
        opp.quoted_at = datetime.now(UTC)
        opp.stage = "prospecto"
        event(opp, "cotizada", actor.user_id, f"{amount} {unit.price_currency}")
        await self.db.commit()
        return read_opportunity(await self.get(opp.id))

    async def deliver(self, opportunity_id, payload, actor):
        opp = await self.get(opportunity_id, True)
        if not opp.sale_id:
            raise BadRequestError("Registra la venta y su factura antes de entregar el vehículo.")
        sale = (
            await self.db.execute(
                select(VehicleSale).where(VehicleSale.id == opp.sale_id).with_for_update()
            )
        ).scalar_one()
        if not sale.delivered_at:
            if opp.workflow_version:
                from app.modules.concesionario.sales_workflow import read_workflow

                if not read_workflow(opp)["delivery_ready"]:
                    raise ConflictError(
                        "Completa la PDI, el acta y todos los puntos de entrega antes de confirmar."
                    )
            sale.delivered_at = datetime.now(UTC)
            sale.delivered_by_user_id = actor.user_id
            sale.delivery_reference = payload.reference.strip()
            if opp.workflow_version and opp.vehicle.condition.value == "nuevo":
                from app.modules.post_ventas.service import PostVentasService

                await PostVentasService(self.db).create_or_renew_warranty_from_sale(
                    opp.filial_id,
                    opp.vehicle.vin,
                    opp.vehicle.brand,
                    opp.vehicle.model,
                    venezuela_today(),
                    opp.vehicle.id,
                    commit=False,
                )
            event(opp, "entregada", actor.user_id, payload.reference.strip())
            await self.db.commit()
        return read_opportunity(await self.get(opp.id))

    async def operation(self, unit, client_id, advisor_id, actor_id, sale=None):
        # Caller already locked the stock unit. Reuse its commercial opportunity.
        query = (
            select(Opportunity)
            .where(
                Opportunity.filial_id == unit.filial_id,
                Opportunity.client_id == client_id,
                Opportunity.sale_id.is_(None),
                Opportunity.stage != "perdida",
                or_(Opportunity.vehicle_id == unit.id, Opportunity.vehicle_id.is_(None)),
            )
            .order_by(
                case((Opportunity.vehicle_id == unit.id, 0), else_=1), Opportunity.created_at.desc()
            )
            .limit(1)
            .with_for_update()
        )
        opp = (await self.db.execute(query)).scalar_one_or_none() if client_id else None
        client = await self.db.get(Client, client_id) if client_id else None
        if not opp:
            opp = Opportunity(
                filial_id=unit.filial_id,
                client_id=client_id,
                client_name=client.full_name if client else sale.client_name if sale else "Cliente",
                origin=client.lead_origin if client else "registro_directo",
                created_by_user_id=actor_id,
                activity=[],
                workflow_version=0,
                version=0,
            )
            self.db.add(opp)
        opp.vehicle_id = unit.id
        opp.vehicle_label = f"{unit.brand} {unit.model} {unit.year}"
        opp.advisor_user_id = advisor_id
        opp.vehicle = unit
        if sale:
            opp.sale_id = sale.id
            opp.sale = sale
        event(opp, "facturada" if sale else "reservada", actor_id)
        await self.db.flush()
        if sale and opp.workflow_version:
            from app.modules.concesionario.sales_workflow import SalesWorkflow

            await SalesWorkflow(self.db).create_pdi(opp, actor_id)
