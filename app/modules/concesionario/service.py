import uuid
from datetime import UTC, datetime

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.core.venezuela_time import venezuela_today
from app.modules.auth.schemas import CurrentUser
from app.modules.clients.models import Client
from app.modules.concesionario.enums import SaleType, VehicleStatus
from app.modules.concesionario.exceptions import (
    BelowCostOverrideNoteRequiredError,
    BelowCostSaleRequiresAuthorizationError,
    InvalidVehicleStatusTransitionError,
    SaleDetailsRequiredError,
    VehicleNotFoundError,
    VehicleReservedByAnotherUserError,
    VinAlreadyExistsError,
)
from app.modules.concesionario.models import DealershipVehicle, VehicleSale
from app.modules.concesionario.schemas import VehicleCreate, VehicleReservationInput, VehicleUpdate
from app.modules.exchange_rates.models import ExchangeRate
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import SUPER_ADMIN_SLUG, ensure_module_access
from app.modules.service_orders.billing import billing_day, calculate_payment

# A vehicle arrives EN_TRANSITO, gets readied (or not) and becomes
# DISPONIBLE, and from there can be RESERVADO or sold outright. A reservation
# either falls through to VENDIDO or is released back to DISPONIBLE. VENDIDO
# is terminal — nothing un-sells a vehicle.
ALLOWED_TRANSITIONS: dict[VehicleStatus, set[VehicleStatus]] = {
    VehicleStatus.EN_TRANSITO: {VehicleStatus.DISPONIBLE, VehicleStatus.EN_PREPARACION},
    VehicleStatus.EN_PREPARACION: {VehicleStatus.DISPONIBLE},
    VehicleStatus.DISPONIBLE: {
        VehicleStatus.EN_PREPARACION,
        VehicleStatus.RESERVADO,
        VehicleStatus.VENDIDO,
    },
    VehicleStatus.RESERVADO: {VehicleStatus.DISPONIBLE, VehicleStatus.VENDIDO},
    VehicleStatus.VENDIDO: set(),
}


class ConcesionarioService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_vehicles(self, filial_id: uuid.UUID, search: str | None = None) -> list[DealershipVehicle]:
        query = (
            select(DealershipVehicle)
            .where(DealershipVehicle.filial_id == filial_id)
            .order_by(DealershipVehicle.created_at.desc())
        )
        result = await self.db.execute(query)
        vehicles = list(result.scalars().all())

        if search:
            term = search.lower()
            vehicles = [
                v
                for v in vehicles
                if term in v.brand.lower()
                or term in v.model.lower()
                or term in v.vin.lower()
                or term in v.sku.lower()
                or term in str(v.year)
                or (v.plate and term in v.plate.lower())
            ]
        return vehicles

    async def get_vehicle(self, vehicle_id: uuid.UUID) -> DealershipVehicle:
        vehicle = await self.db.get(DealershipVehicle, vehicle_id)
        if vehicle is None:
            raise VehicleNotFoundError(str(vehicle_id))
        return vehicle

    async def create_vehicle(self, payload: VehicleCreate) -> DealershipVehicle:
        await self._ensure_vin_available(payload.filial_id, payload.vin)
        vehicle = DealershipVehicle(**payload.model_dump())
        self.db.add(vehicle)
        await self.db.commit()
        await self.db.refresh(vehicle)
        return vehicle

    async def _require_vehicle_for_update(self, vehicle_id: uuid.UUID) -> DealershipVehicle:
        result = await self.db.execute(
            select(DealershipVehicle)
            .where(DealershipVehicle.id == vehicle_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        vehicle = result.scalar_one_or_none()
        if vehicle is None:
            raise VehicleNotFoundError(str(vehicle_id))
        return vehicle

    def _ensure_not_reserved_by_someone_else(
        self, vehicle: DealershipVehicle, current_user: CurrentUser
    ) -> None:
        if (
            vehicle.status == VehicleStatus.RESERVADO
            and vehicle.reserved_by_user_id is not None
            and vehicle.reserved_by_user_id != current_user.user_id
            and current_user.role_slug != SUPER_ADMIN_SLUG
        ):
            raise VehicleReservedByAnotherUserError()

    async def reserve_vehicle(
        self, vehicle_id: uuid.UUID, payload: VehicleReservationInput, current_user: CurrentUser
    ) -> DealershipVehicle:
        vehicle = await self._require_vehicle_for_update(vehicle_id)
        self._ensure_not_reserved_by_someone_else(vehicle, current_user)

        if VehicleStatus.RESERVADO not in ALLOWED_TRANSITIONS.get(vehicle.status, set()):
            raise InvalidVehicleStatusTransitionError(vehicle.status.value, VehicleStatus.RESERVADO.value)

        if payload.expires_at < venezuela_today():
            raise BadRequestError("La vigencia de la reserva no puede ser una fecha pasada.")

        client = await self.db.get(Client, payload.client_id)
        if client is None or client.filial_id != vehicle.filial_id:
            raise BadRequestError("El cliente no pertenece a esta filial.")

        vehicle.status = VehicleStatus.RESERVADO
        vehicle.reserved_client_id = payload.client_id
        vehicle.reserved_by_user_id = payload.advisor_user_id
        vehicle.deposit_amount = payload.deposit_amount
        vehicle.reservation_expires_at = payload.expires_at
        vehicle.reserved_at = datetime.now(UTC)

        await self.db.commit()
        await self.db.refresh(vehicle)
        return vehicle

    async def update_vehicle(
        self, vehicle_id: uuid.UUID, payload: VehicleUpdate, current_user: CurrentUser
    ) -> DealershipVehicle:
        if payload.status == VehicleStatus.RESERVADO:
            raise BadRequestError(
                "Reservar un vehículo requiere el formulario de reserva; usa esa acción."
            )

        vehicle = await self._require_vehicle_for_update(vehicle_id)
        self._ensure_not_reserved_by_someone_else(vehicle, current_user)

        status_changing = payload.status is not None and payload.status != vehicle.status
        if status_changing and payload.status not in ALLOWED_TRANSITIONS.get(vehicle.status, set()):
            raise InvalidVehicleStatusTransitionError(vehicle.status.value, payload.status.value)

        was_reserved = vehicle.status == VehicleStatus.RESERVADO
        becoming_sold = status_changing and payload.status == VehicleStatus.VENDIDO

        if becoming_sold:
            if payload.sale is None:
                raise SaleDetailsRequiredError()

            sequence_result = await self.db.execute(
                select(func.max(VehicleSale.sequence_number)).where(VehicleSale.filial_id == vehicle.filial_id)
            )
            next_sequence = (sequence_result.scalar() or 0) + 1

            # The client never gets to hand us a total — IGTF depends on how
            # much actually lands in foreign currency, which is only known
            # (and only trustworthy) computed here, the same way
            # service_orders/billing.py never accepts a submitted invoice
            # amount either.
            igtf_amount = 0.0
            bcv_rate: float | None = None
            usd_base_value: float | None = None
            if payload.sale.sale_type == SaleType.CONTADO:
                rate_row = None
                if payload.sale.payment_method in ("bs", "mixed"):
                    rate_row = (
                        await self.db.execute(
                            select(ExchangeRate).where(
                                ExchangeRate.currency == "USD", ExchangeRate.value_date == billing_day()
                            )
                        )
                    ).scalar_one_or_none()
                    bcv_rate = float(rate_row.rate_ves) if rate_row else None
                payment = calculate_payment(
                    Decimal(str(vehicle.cash_total)),
                    payload.sale.payment_method,
                    Decimal(str(payload.sale.usd_base or 0)),
                    vehicle.igtf_percentage,
                    Decimal(str(bcv_rate)) if bcv_rate else None,
                )
                igtf_amount = payment["igtf_amount"]
                usd_base_value = payment["usd_base"]
                final_price = payment["total_usd"]
            else:
                final_price = vehicle.price_financed

            is_below_cost = vehicle.cost_price is not None and final_price < float(vehicle.cost_price)
            authorized_by_user_id: uuid.UUID | None = None
            authorized_at: datetime | None = None
            if is_below_cost:
                if not payload.sale.below_cost_override:
                    raise BelowCostSaleRequiresAuthorizationError()
                if not payload.sale.below_cost_override_note:
                    raise BelowCostOverrideNoteRequiredError()
                # An asesor/vendedor cannot authorize their own below-cost
                # sale — the same "administracion" bar used to authorize a
                # warranty claim, deliberately higher than concesionario
                # EDITAR (which is what let them get this far).
                await ensure_module_access(
                    self.db, current_user, vehicle.filial_id, "administracion", AccessLevel.EDITAR
                )
                authorized_by_user_id = current_user.user_id
                authorized_at = datetime.now(UTC)

            sale_id = uuid.uuid4()
            self.db.add(
                VehicleSale(
                    id=sale_id,
                    filial_id=vehicle.filial_id,
                    sequence_number=next_sequence,
                    vehicle_id=vehicle.id,
                    client_name=payload.sale.client_name,
                    client_document=payload.sale.client_document,
                    advisor_user_id=payload.sale.advisor_user_id,
                    sale_type=payload.sale.sale_type,
                    payment_method=payload.sale.payment_method,
                    usd_base=usd_base_value,
                    igtf_amount=igtf_amount,
                    bcv_rate=bcv_rate,
                    final_price=final_price,
                    below_cost_override=is_below_cost,
                    below_cost_override_note=payload.sale.below_cost_override_note if is_below_cost else None,
                    authorized_by_user_id=authorized_by_user_id,
                    authorized_at=authorized_at,
                )
            )

            from app.modules.administracion.enums import MovementSourceType
            from app.modules.administracion.service import AdministracionService

            admin_service = AdministracionService(self.db)
            await admin_service.record_automatic_income(
                vehicle.filial_id,
                f"Venta de vehículo pagada · {payload.sale.client_name}",
                final_price,
                f"CV-{next_sequence:04d}",
                source_type=MovementSourceType.VEHICLE_SALE,
                source_id=sale_id,
            )

            from app.modules.post_ventas.service import PostVentasService

            await PostVentasService(self.db).create_or_renew_warranty_from_sale(
                vehicle.filial_id,
                vehicle.vin,
                vehicle.brand,
                vehicle.model,
                venezuela_today(),
                vehicle.id,
            )

        for field in (
            "status",
            "condition",
            "brand",
            "model",
            "year",
            "color",
            "fuel_type",
            "transmission",
            "plate",
            "price_cash",
            "price_financed",
            "cost_price",
            "cost_is_estimated",
            "price_currency",
            "iva_percentage",
            "igtf_percentage",
            "luxury_tax_percentage",
            "financing_provider",
            "financing_external_id",
        ):
            value = getattr(payload, field)
            if value is not None:
                setattr(vehicle, field, value)

        if status_changing and was_reserved:
            # Leaving RESERVADO (released back to disponible, or sold)
            # clears the transient hold — it isn't kept as history.
            vehicle.reserved_client_id = None
            vehicle.reserved_by_user_id = None
            vehicle.deposit_amount = None
            vehicle.reservation_expires_at = None
            vehicle.reserved_at = None

        await self.db.commit()
        await self.db.refresh(vehicle)
        return vehicle

    async def delete_vehicle(self, vehicle_id: uuid.UUID) -> None:
        vehicle = await self.get_vehicle(vehicle_id)
        await self.db.delete(vehicle)
        await self.db.commit()

    async def list_sales(self, filial_id: uuid.UUID) -> list[VehicleSale]:
        result = await self.db.execute(
            select(VehicleSale)
            .where(VehicleSale.filial_id == filial_id)
            .order_by(VehicleSale.created_at.desc())
        )
        return list(result.scalars().all())

    async def _ensure_vin_available(self, filial_id: uuid.UUID, vin: str) -> None:
        result = await self.db.execute(
            select(DealershipVehicle).where(
                DealershipVehicle.filial_id == filial_id, DealershipVehicle.vin == vin
            )
        )
        if result.scalar_one_or_none() is not None:
            raise VinAlreadyExistsError(vin)
