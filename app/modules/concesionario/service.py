import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.concesionario.enums import VehicleStatus
from app.modules.concesionario.exceptions import (
    SaleDetailsRequiredError,
    VehicleNotFoundError,
    VinAlreadyExistsError,
)
from app.modules.concesionario.models import DealershipVehicle, VehicleSale
from app.modules.concesionario.schemas import VehicleCreate, VehicleUpdate


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

    async def update_vehicle(self, vehicle_id: uuid.UUID, payload: VehicleUpdate) -> DealershipVehicle:
        vehicle = await self.get_vehicle(vehicle_id)
        becoming_sold = (
            payload.status == VehicleStatus.VENDIDO and vehicle.status != VehicleStatus.VENDIDO
        )

        if becoming_sold:
            if payload.sale is None:
                raise SaleDetailsRequiredError()

            sequence_result = await self.db.execute(
                select(func.max(VehicleSale.sequence_number)).where(VehicleSale.filial_id == vehicle.filial_id)
            )
            next_sequence = (sequence_result.scalar() or 0) + 1

            self.db.add(
                VehicleSale(
                    filial_id=vehicle.filial_id,
                    sequence_number=next_sequence,
                    vehicle_id=vehicle.id,
                    client_name=payload.sale.client_name,
                    client_document=payload.sale.client_document,
                    advisor_user_id=payload.sale.advisor_user_id,
                    sale_type=payload.sale.sale_type,
                    final_price=payload.sale.final_price,
                )
            )

            from app.modules.administracion.service import AdministracionService

            admin_service = AdministracionService(self.db)
            await admin_service.record_automatic_income(
                vehicle.filial_id,
                f"Venta de vehículo pagada · {payload.sale.client_name}",
                payload.sale.final_price,
                f"CV-{next_sequence:04d}",
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
