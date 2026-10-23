"""Warranty-first claims and local approval, shared by web and mobile."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypedDict

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.venezuela_time import VENEZUELA_TZ, venezuela_today
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


class ClaimsOverview(TypedDict):
    pending: int
    monthly_cost: float
    monthly_reworks: int
    monthly_closed: int
    comeback_rate: float


class ClaimsWorkflow:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.service = ServiceOrderService(db)

    async def create(self, payload, photos, documents, actor, *, odometer_photo_url=None):
        vehicle = await self.service._get_vehicle_with_client(payload.vehicle_id)
        from app.modules.clients.mileage import validate_mileage

        await validate_mileage(self.db, payload.vehicle_id, payload.reported_mileage)
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
                "start_mileage": (
                    warranty.expiration_mileage - warranty.duration_km
                    if warranty.expiration_mileage is not None and warranty.duration_km is not None
                    else None
                ),
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
                "start_mileage": 0,
                "expiration_mileage": warranty.duration_km,
                "duration_km": warranty.duration_km,
                "starts_at": warranty.starts_at.isoformat(),
                "expires_at": warranty.expires_at.isoformat() if warranty.expires_at else None,
            }
            kind = WarrantyClaimType.FABRICA
        result = await self.service.create_warranty_claim(
            WarrantyClaimCreate(
                odometer_photo_url=odometer_photo_url,
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
        claim.brand_status = None
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
        # Old en_marca records can be decided through the same local review; no new brand state.
        if (
            claim.status not in (WarrantyClaimStatus.SOLICITADO, WarrantyClaimStatus.AUTORIZADO)
            or claim.resulting_service_order_id is not None
        ):
            raise ConflictError("Este reclamo ya fue revisado.")
        if payload.decision == "aprobado" and claim.claim_type == WarrantyClaimType.COMEBACK:
            if not payload.failure_confirmed:
                raise BadRequestError(
                    "Confirma que la falla está relacionada con el trabajo reclamado."
                )
            if payload.failure_category is None:
                raise BadRequestError("Selecciona la causa de la falla.")
        claim.authorized_at = datetime.now(UTC)
        claim.authorized_by_user_id = actor.user_id
        claim.brand_status = None
        if payload.decision == "rechazado":
            claim.status = WarrantyClaimStatus.RECHAZADO
            claim.rejection_reason = payload.rejection_reason.strip()
        else:
            claim.status = WarrantyClaimStatus.AUTORIZADO
            claim.failure_confirmed = (
                payload.failure_confirmed
                if claim.claim_type == WarrantyClaimType.COMEBACK
                else False
            )
            if claim.claim_type == WarrantyClaimType.COMEBACK:
                claim.failure_category = payload.failure_category
            await self.db.flush()
            await self.service.convert_warranty_claim_to_order(
                claim.id,
                WarrantyClaimConvertInput(intake_mileage=claim.reported_mileage),
                actor.user_id,
                commit=False,
                as_draft=True,
            )
        await self.db.commit()
        return await self.service.get_warranty_claim(claim.id)

    async def overview(self, filial_id: uuid.UUID) -> ClaimsOverview:
        """Aggregate dashboard data without loading the entire claim history.

        The query count stays constant as the number of reworks grows. Parts
        and labor are aggregated separately to avoid multiplying their totals
        when an order has several tasks and several transfer lines.
        """
        month_start = datetime.combine(
            venezuela_today().replace(day=1), datetime.min.time(), tzinfo=VENEZUELA_TZ
        ).astimezone(UTC)
        pending = (
            await self.db.execute(
                select(func.count())
                .select_from(WarrantyClaim)
                .where(
                    WarrantyClaim.filial_id == filial_id,
                    or_(
                        WarrantyClaim.status == WarrantyClaimStatus.SOLICITADO,
                        and_(
                            WarrantyClaim.status == WarrantyClaimStatus.AUTORIZADO,
                            WarrantyClaim.brand_status == "en_marca",
                        ),
                    ),
                )
            )
        ).scalar_one()
        reworks = (
            await self.db.execute(
                select(ServiceOrder.id, ServiceOrder.settings_snapshot)
                .join(WarrantyClaim, WarrantyClaim.resulting_service_order_id == ServiceOrder.id)
                .where(
                    WarrantyClaim.filial_id == filial_id,
                    ServiceOrder.filial_id == filial_id,
                    WarrantyClaim.claim_type == WarrantyClaimType.COMEBACK,
                    WarrantyClaim.converted_at >= month_start,
                )
            )
        ).all()
        closed = (
            await self.db.execute(
                select(func.count())
                .select_from(ServiceOrder)
                .where(
                    ServiceOrder.filial_id == filial_id,
                    ServiceOrder.status == ServiceOrderStatus.ORDEN_CERRADA,
                    ServiceOrder.closed_at >= month_start,
                )
            )
        ).scalar_one()
        cost = Decimal(0)
        if reworks:
            order_ids = {order_id for order_id, _settings in reworks}
            parts_by_order = dict(
                (
                    await self.db.execute(
                        select(
                            ServiceOrderTransfer.service_order_id,
                            func.sum(ServiceOrderTransferLine.cost_total),
                        )
                        .join(
                            ServiceOrderTransferLine,
                            ServiceOrderTransferLine.transfer_id == ServiceOrderTransfer.id,
                        )
                        .where(
                            ServiceOrderTransfer.service_order_id.in_(order_ids),
                            ServiceOrderTransfer.stock_deducted.is_(True),
                        )
                        .group_by(ServiceOrderTransfer.service_order_id)
                    )
                ).all()
            )
            hours_by_order = dict(
                (
                    await self.db.execute(
                        select(
                            ServiceOrderTask.service_order_id,
                            func.sum(ServiceOrderTask.hours_snapshot),
                        )
                        .where(
                            ServiceOrderTask.service_order_id.in_(order_ids),
                            ServiceOrderTask.status == TaskStatus.COMPLETADA,
                        )
                        .group_by(ServiceOrderTask.service_order_id)
                    )
                ).all()
            )
            for order_id, settings in reworks:
                # Preserve the ODS rate snapshot; neither the live catalog nor
                # the customer's retail margin changes historical direct costs.
                parts_cost = Decimal(str(parts_by_order.get(order_id) or 0))
                completed_hours = Decimal(str(hours_by_order.get(order_id) or 0))
                rate = Decimal(str((settings or {}).get("hourly_rate", 0)))
                cost += parts_cost + completed_hours * rate
        return {
            "pending": pending,
            "monthly_cost": float(cost),
            "monthly_reworks": len(reworks),
            "monthly_closed": closed,
            "comeback_rate": round(100 * len(reworks) / closed, 2) if closed else 0,
        }
