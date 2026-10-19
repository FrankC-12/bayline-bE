"""Warranty-first web claims; mobile's existing contract remains supported."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.venezuela_time import VENEZUELA_TZ, caracas_date, venezuela_today
from app.modules.post_ventas.models import VehicleWarranty, WorkshopWarranty
from app.modules.service_orders.enums import (
    ServiceOrderStatus,
    TaskStatus,
    WarrantyClaimStatus,
    WarrantyClaimType,
)
from app.modules.service_orders.models import (
    ServiceOrder,
    ServiceOrderTask,
    ServiceOrderTransfer,
    ServiceOrderTransferLine,
    WarrantyClaim,
)
from app.modules.service_orders.schemas import WarrantyClaimConvertInput, WarrantyClaimCreate
from app.modules.service_orders.service import ServiceOrderService


class ClaimsWorkflow:
    def __init__(self, db):
        self.db = db
        self.service = ServiceOrderService(db)

    async def create(self, payload, photos, documents, actor):
        vehicle = await self.service._get_vehicle_with_client(payload.vehicle_id)
        today = venezuela_today()
        source = task_id = None
        coverage = {"label": "Campaña o recall", "kind": "campaign"}
        kind = WarrantyClaimType.CAMPANA_RECALL
        if payload.workshop_warranty_id:
            warranty = await self.db.get(WorkshopWarranty, payload.workshop_warranty_id)
            if (
                warranty is None
                or warranty.filial_id != vehicle.client.filial_id
                or warranty.vin != vehicle.vin
            ):
                raise BadRequestError("La garantía no pertenece a este vehículo.")
            if (
                warranty.starts_at > today
                or (warranty.expires_at and today > warranty.expires_at)
                or (
                    warranty.expiration_mileage is not None
                    and payload.reported_mileage >= warranty.expiration_mileage
                )
            ):
                raise ConflictError(
                    "La garantía del taller está fuera de cobertura.", error_code="warranty_expired"
                )
            coverage = {
                "label": warranty.tempario_name_snapshot,
                "kind": "workshop",
                "duration_days": warranty.duration_days,
                "duration_km": warranty.duration_km,
                "starts_at": warranty.starts_at.isoformat(),
                "expires_at": warranty.expires_at.isoformat() if warranty.expires_at else None,
                "expiration_mileage": warranty.expiration_mileage,
                "coverage_type": warranty.coverage_type.value,
            }
            task = await self.db.get(ServiceOrderTask, warranty.service_order_task_id)
            task_id, source, kind = (
                task.tempario_id,
                warranty.service_order_id,
                WarrantyClaimType.COMEBACK,
            )
        elif payload.factory_warranty_id:
            warranty = await self.db.get(VehicleWarranty, payload.factory_warranty_id)
            if (
                warranty is None
                or warranty.filial_id != vehicle.client.filial_id
                or warranty.vin != vehicle.vin
            ):
                raise BadRequestError("La garantía no pertenece a este vehículo.")
            if (
                warranty.starts_at > today
                or (warranty.expires_at and today > warranty.expires_at)
                or (
                    warranty.duration_km is not None
                    and payload.reported_mileage >= warranty.duration_km
                )
            ):
                raise ConflictError(
                    "La garantía de fábrica está fuera de cobertura.", error_code="warranty_expired"
                )
            coverage = {
                "label": "Garantía de fábrica",
                "kind": "factory",
                "duration_months": warranty.duration_months,
                "duration_km": warranty.duration_km,
                "starts_at": warranty.starts_at.isoformat(),
                "expires_at": warranty.expires_at.isoformat() if warranty.expires_at else None,
            }
            kind = WarrantyClaimType.FABRICA
        result = await self.service.create_warranty_claim(
            WarrantyClaimCreate(
                vehicle_id=vehicle.id,
                claim_type=kind,
                service_order_id=source,
                tempario_id=task_id,
                reported_mileage=payload.reported_mileage,
                claimed_at=today,
                failure_cause=payload.symptom if kind == WarrantyClaimType.COMEBACK else None,
                reported_symptom=payload.symptom,
            ),
            photos,
            documents,
            actor.user_id,
            commit=False,
        )
        claim = await self.db.get(WarrantyClaim, result.id)
        claim.coverage_snapshot = coverage
        claim.workshop_warranty_id = payload.workshop_warranty_id
        claim.factory_warranty_id = payload.factory_warranty_id
        claim.brand_status = "pendiente" if kind != WarrantyClaimType.COMEBACK else None
        await self.db.commit()
        return await self.service.get_warranty_claim(claim.id)

    async def review(self, claim_id, payload, actor, *, brand=False):
        claim = (
            await self.db.execute(
                select(WarrantyClaim)
                .where(WarrantyClaim.id == claim_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if claim is None:
            raise NotFoundError("Reclamo no encontrado.")
        if brand:
            if claim.brand_status != "en_marca" or claim.status != WarrantyClaimStatus.AUTORIZADO:
                raise ConflictError("El reclamo no está pendiente de respuesta de la marca.")
            claim.brand_response_at = datetime.now(UTC)
            claim.brand_responded_by_user_id = actor.user_id
            claim.brand_status = "autorizado" if payload.decision == "aprobado" else "rechazado"
        elif claim.status != WarrantyClaimStatus.SOLICITADO:
            raise ConflictError("Este reclamo ya fue revisado.")
        if not brand:
            claim.authorized_at = datetime.now(UTC)
            claim.authorized_by_user_id = actor.user_id
        if payload.decision == "rechazado":
            claim.status = WarrantyClaimStatus.RECHAZADO
            claim.rejection_reason = payload.rejection_reason.strip()
        else:
            claim.status = WarrantyClaimStatus.AUTORIZADO
            if claim.claim_type == WarrantyClaimType.COMEBACK:
                if payload.failure_category is None:
                    raise BadRequestError("Selecciona la causa de la falla.")
                claim.failure_category = payload.failure_category
                await self.service.convert_warranty_claim_to_order(
                    claim.id,
                    WarrantyClaimConvertInput(intake_mileage=claim.reported_mileage),
                    actor.user_id,
                    commit=False,
                )
            elif brand:
                await self.service.convert_warranty_claim_to_order(
                    claim.id,
                    WarrantyClaimConvertInput(intake_mileage=claim.reported_mileage),
                    actor.user_id,
                    commit=False,
                )
            else:
                claim.brand_status = "en_marca"
                claim.brand_sent_at = datetime.now(UTC)
        await self.db.commit()
        return await self.service.get_warranty_claim(claim.id)

    async def overview(self, filial_id):
        today = venezuela_today()
        start = today.replace(day=1)
        claims = list(
            (
                await self.db.execute(
                    select(WarrantyClaim).where(WarrantyClaim.filial_id == filial_id)
                )
            ).scalars()
        )
        reworks = [
            c
            for c in claims
            if c.claim_type == WarrantyClaimType.COMEBACK
            and c.resulting_service_order_id
            and c.converted_at
            and caracas_date(c.converted_at) >= start
        ]
        closed = (
            await self.db.execute(
                select(func.count())
                .select_from(ServiceOrder)
                .where(
                    ServiceOrder.filial_id == filial_id,
                    ServiceOrder.status == ServiceOrderStatus.ORDEN_CERRADA,
                    ServiceOrder.closed_at
                    >= datetime.combine(start, datetime.min.time(), tzinfo=VENEZUELA_TZ),
                )
            )
        ).scalar() or 0
        cost = Decimal(0)
        for claim in reworks:
            order = await self.db.get(ServiceOrder, claim.resulting_service_order_id)
            lines = (
                await self.db.execute(
                    select(ServiceOrderTransferLine)
                    .join(
                        ServiceOrderTransfer,
                        ServiceOrderTransfer.id == ServiceOrderTransferLine.transfer_id,
                    )
                    .where(
                        ServiceOrderTransfer.service_order_id == order.id,
                        ServiceOrderTransfer.stock_deducted.is_(True),
                    )
                )
            ).scalars()
            cost += sum((Decimal(str(line.cost_total)) for line in lines), Decimal(0))
            # Only completed work is a cost; use the ODS rate snapshot, never the live catalog or retail margin.
            tasks = (
                await self.db.execute(
                    select(ServiceOrderTask).where(
                        ServiceOrderTask.service_order_id == order.id,
                        ServiceOrderTask.status == TaskStatus.COMPLETADA,
                    )
                )
            ).scalars()
            rate = Decimal(str((order.settings_snapshot or {}).get("hourly_rate", 0)))
            cost += sum((Decimal(str(t.hours_snapshot)) * rate for t in tasks), Decimal(0))
        return {
            "pending": sum(c.status == WarrantyClaimStatus.SOLICITADO for c in claims),
            "monthly_cost": float(cost),
            "monthly_reworks": len(reworks),
            "monthly_closed": closed,
            "comeback_rate": round(100 * len(reworks) / closed, 2) if closed else 0,
        }
