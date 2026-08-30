import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.clients.exceptions import ClientNotFoundError, DocumentAlreadyExistsError
from app.modules.clients.models import Client, Vehicle
from app.modules.clients.schemas import ClientCreate, ClientUpdate, VehicleInput


class ClientService:
    """Business logic for clients and their vehicles within a filial."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_clients(self, filial_id: uuid.UUID, search: str | None = None) -> list[Client]:
        query = (
            select(Client)
            .options(selectinload(Client.vehicles))
            .where(Client.filial_id == filial_id)
            .order_by(Client.full_name)
        )
        result = await self.db.execute(query)
        clients = list(result.scalars().all())

        if search:
            term = search.lower()
            clients = [
                c
                for c in clients
                if term in c.full_name.lower()
                or term in c.document_number.lower()
                or any(
                    term in (v.plate or "").lower() or term in (v.vin or "").lower()
                    for v in c.vehicles
                )
            ]
        return clients

    async def get_client(self, client_id: uuid.UUID) -> Client:
        query = select(Client).options(selectinload(Client.vehicles)).where(Client.id == client_id)
        result = await self.db.execute(query)
        client = result.scalar_one_or_none()
        if client is None:
            raise ClientNotFoundError(str(client_id))
        return client

    async def create_client(self, payload: ClientCreate) -> Client:
        await self._ensure_document_is_available(payload.filial_id, payload.document_number)

        client = Client(
            filial_id=payload.filial_id,
            full_name=payload.full_name,
            client_type=payload.client_type,
            document_type=payload.document_type,
            document_number=payload.document_number,
            email=payload.email,
            phone_primary=payload.phone_primary,
            phone_secondary=payload.phone_secondary,
            contact_preference=payload.contact_preference,
            address=payload.address,
            address_type=payload.address_type,
        )
        self.db.add(client)
        await self.db.flush()

        for v in payload.vehicles:
            self.db.add(self._build_vehicle(client.id, v))

        await self.db.commit()
        return await self.get_client(client.id)

    async def update_client(self, client_id: uuid.UUID, payload: ClientUpdate) -> Client:
        client = await self.get_client(client_id)

        if payload.document_number and payload.document_number != client.document_number:
            await self._ensure_document_is_available(client.filial_id, payload.document_number)
            client.document_number = payload.document_number

        for field in (
            "full_name",
            "client_type",
            "document_type",
            "email",
            "phone_primary",
            "phone_secondary",
            "contact_preference",
            "address",
            "address_type",
        ):
            value = getattr(payload, field)
            if value is not None:
                setattr(client, field, value)

        if payload.vehicles is not None:
            await self._reconcile_vehicles(client, payload.vehicles)

        await self.db.commit()
        return await self.get_client(client.id)

    async def delete_client(self, client_id: uuid.UUID) -> None:
        client = await self.get_client(client_id)
        await self.db.delete(client)
        await self.db.commit()

    async def _reconcile_vehicles(self, client: Client, vehicles: list[VehicleInput]) -> None:
        """Diffs the incoming vehicle list against what's stored: deletes the ones
        missing, updates the ones matched by id, and creates the rest as new."""
        existing_by_id = {v.id: v for v in client.vehicles}
        incoming_ids = {v.id for v in vehicles if v.id is not None}

        for existing_id, existing_vehicle in list(existing_by_id.items()):
            if existing_id not in incoming_ids:
                await self.db.delete(existing_vehicle)

        for v in vehicles:
            if v.id and v.id in existing_by_id:
                vehicle = existing_by_id[v.id]
                vehicle.brand = v.brand
                vehicle.model = v.model
                vehicle.year = v.year
                vehicle.vin = v.vin
                vehicle.mileage = v.mileage
                vehicle.purchase_date = v.purchase_date
                vehicle.body_type = v.body_type
                vehicle.plate = v.plate
                vehicle.color = v.color
                vehicle.upholstery = v.upholstery
                vehicle.fuel_type = v.fuel_type
                vehicle.transmission = v.transmission
            else:
                self.db.add(self._build_vehicle(client.id, v))

    def _build_vehicle(self, client_id: uuid.UUID, v: VehicleInput) -> Vehicle:
        return Vehicle(
            client_id=client_id,
            brand=v.brand,
            model=v.model,
            year=v.year,
            vin=v.vin,
            mileage=v.mileage,
            purchase_date=v.purchase_date,
            body_type=v.body_type,
            plate=v.plate,
            color=v.color,
            upholstery=v.upholstery,
            fuel_type=v.fuel_type,
            transmission=v.transmission,
        )

    async def _ensure_document_is_available(self, filial_id: uuid.UUID, document_number: str) -> None:
        result = await self.db.execute(
            select(Client).where(
                Client.filial_id == filial_id, Client.document_number == document_number
            )
        )
        if result.scalar_one_or_none() is not None:
            raise DocumentAlreadyExistsError(document_number)