import calendar
import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError
from app.modules.clients.enums import MaintenancePlanEntryStatus
from app.modules.clients.exceptions import (
    ClientNotFoundError,
    DocumentAlreadyExistsError,
    VehicleNotFoundError,
)
from app.modules.clients.models import Client, Vehicle
from app.modules.clients.schemas import (
    ClientCreate,
    ClientUpdate,
    VehicleInput,
    VehiclePlanEntryStatusRead,
    VehiclePlanStatusRead,
)
from app.modules.inspections.models import PreliminaryInspection


def _add_months(base: date, months: int) -> date:
    total = base.month - 1 + months
    year = base.year + total // 12
    month = total % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


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
        await self._attach_current_mileage(clients)
        await self._attach_next_maintenance_plan(clients)
        return clients

    async def get_client(self, client_id: uuid.UUID) -> Client:
        query = select(Client).options(selectinload(Client.vehicles)).where(Client.id == client_id)
        result = await self.db.execute(query)
        client = result.scalar_one_or_none()
        if client is None:
            raise ClientNotFoundError(str(client_id))
        await self._attach_current_mileage([client])
        await self._attach_next_maintenance_plan([client])
        return client

    async def _attach_current_mileage(self, clients: list[Client]) -> None:
        """Sets current_mileage/_visit_date/_service_order_id/_code as plain
        attributes on each Vehicle, sourced from the most recent
        PreliminaryInspection that recorded a mileage for it. These aren't
        mapped columns — VehicleRead picks them up via from_attributes, same
        as any other field. The registration `mileage` field itself is never
        touched here.

        If the latest such inspection isn't linked to a service order yet,
        the order id/code are left None instead of falling back to an older,
        order-linked inspection — mixing mileage/date from one visit with a
        link to a different one would be wrong.
        """
        vehicle_ids = [v.id for c in clients for v in c.vehicles]
        latest = await self._latest_mileage_inspections(vehicle_ids)

        order_ids = {i.service_order_id for i in latest.values() if i.service_order_id}
        orders_by_id: dict[uuid.UUID, int] = {}
        if order_ids:
            from app.modules.service_orders.models import ServiceOrder

            rows = await self.db.execute(
                select(ServiceOrder.id, ServiceOrder.sequence_number).where(
                    ServiceOrder.id.in_(order_ids)
                )
            )
            orders_by_id = {row.id: row.sequence_number for row in rows}

        for client in clients:
            for vehicle in client.vehicles:
                inspection = latest.get(vehicle.id)
                vehicle.current_mileage = inspection.mileage if inspection else None
                vehicle.current_mileage_visit_date = (
                    inspection.created_at.date() if inspection else None
                )
                vehicle.current_mileage_service_order_id = (
                    inspection.service_order_id if inspection else None
                )
                sequence_number = orders_by_id.get(inspection.service_order_id) if inspection else None
                vehicle.current_mileage_service_order_code = (
                    f"ODS-{sequence_number}" if sequence_number is not None else None
                )

    async def _attach_next_maintenance_plan(self, clients: list[Client]) -> None:
        """Denormalizes the advisor-suggested next-visit tempario (set at ODS
        close time, see ServiceOrderService.close_order) and the assigned
        MaintenancePlan's brand/name onto each Vehicle as plain attributes —
        lets the frontend show both without a second round trip per vehicle."""
        from app.modules.post_ventas.models import MaintenancePlan, Tempario

        tempario_ids = {
            v.next_maintenance_tempario_id
            for c in clients
            for v in c.vehicles
            if v.next_maintenance_tempario_id is not None
        }
        temparios_by_id: dict[uuid.UUID, Tempario] = {}
        if tempario_ids:
            rows = await self.db.execute(select(Tempario).where(Tempario.id.in_(tempario_ids)))
            temparios_by_id = {t.id: t for t in rows.scalars()}

        plan_ids = {
            v.maintenance_plan_id
            for c in clients
            for v in c.vehicles
            if v.maintenance_plan_id is not None
        }
        plans_by_id: dict[uuid.UUID, MaintenancePlan] = {}
        if plan_ids:
            rows = await self.db.execute(
                select(MaintenancePlan).where(MaintenancePlan.id.in_(plan_ids))
            )
            plans_by_id = {p.id: p for p in rows.scalars()}

        for client in clients:
            for vehicle in client.vehicles:
                tempario = temparios_by_id.get(vehicle.next_maintenance_tempario_id)
                vehicle.next_maintenance_tempario_code = tempario.code if tempario else None
                vehicle.next_maintenance_tempario_name = tempario.name if tempario else None
                plan = plans_by_id.get(vehicle.maintenance_plan_id)
                vehicle.maintenance_plan_brand = plan.brand if plan else None
                vehicle.maintenance_plan_name = plan.name if plan else None

    async def _latest_mileage_inspections(
        self, vehicle_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, PreliminaryInspection]:
        if not vehicle_ids:
            return {}
        query = (
            select(PreliminaryInspection)
            .where(
                PreliminaryInspection.vehicle_id.in_(vehicle_ids),
                PreliminaryInspection.mileage.isnot(None),
            )
            .order_by(PreliminaryInspection.vehicle_id, PreliminaryInspection.created_at.desc())
        )
        result = await self.db.execute(query)
        latest: dict[uuid.UUID, PreliminaryInspection] = {}
        for inspection in result.scalars():
            latest.setdefault(inspection.vehicle_id, inspection)
        return latest

    async def get_current_mileage(self, vehicle_id: uuid.UUID) -> int | None:
        latest = await self._latest_mileage_inspections([vehicle_id])
        inspection = latest.get(vehicle_id)
        return inspection.mileage if inspection else None

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
            is_holding_billing=payload.is_holding_billing,
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
            "is_holding_billing",
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

    # Vehicle maintenance plan

    async def get_vehicle(self, vehicle_id: uuid.UUID) -> Vehicle:
        vehicle = await self.db.get(Vehicle, vehicle_id)
        if vehicle is None:
            raise VehicleNotFoundError(str(vehicle_id))
        return vehicle

    async def assign_maintenance_plan(
        self, vehicle_id: uuid.UUID, plan_id: uuid.UUID | None
    ) -> VehiclePlanStatusRead:
        vehicle = await self.get_vehicle(vehicle_id)

        if plan_id is not None:
            from app.modules.post_ventas.models import MaintenancePlan

            client = await self.db.get(Client, vehicle.client_id)
            plan = await self.db.get(MaintenancePlan, plan_id)
            if plan is None or client is None or plan.filial_id != client.filial_id:
                raise BadRequestError("El plan no pertenece a la filial de este vehículo.")

        vehicle.maintenance_plan_id = plan_id
        await self.db.commit()
        return await self.get_vehicle_plan_status(vehicle_id)

    async def get_vehicle_plan_status(self, vehicle_id: uuid.UUID) -> VehiclePlanStatusRead:
        """Computes each plan entry's status live — never stored — from the
        vehicle's current mileage/age and its Service Order history, so it
        can never drift out of sync with reality."""
        from app.modules.post_ventas.models import MaintenancePlan, MaintenancePlanEntry
        from app.modules.service_orders.enums import TaskStatus
        from app.modules.service_orders.models import ServiceOrder, ServiceOrderTask

        vehicle = await self.get_vehicle(vehicle_id)

        current_mileage = (await self._latest_mileage_inspections([vehicle.id])).get(vehicle.id)
        current_mileage = current_mileage.mileage if current_mileage else vehicle.mileage
        reference_date = vehicle.purchase_date or vehicle.created_at.date()

        if vehicle.maintenance_plan_id is None:
            return VehiclePlanStatusRead(
                vehicle_id=vehicle.id,
                plan_id=None,
                plan_brand=None,
                plan_name=None,
                current_mileage=current_mileage,
                reference_date=reference_date,
                entries=[],
            )

        plan = await self.db.get(
            MaintenancePlan,
            vehicle.maintenance_plan_id,
            options=[selectinload(MaintenancePlan.entries).selectinload(MaintenancePlanEntry.tempario)],
        )
        if plan is None:
            return VehiclePlanStatusRead(
                vehicle_id=vehicle.id,
                plan_id=None,
                plan_brand=None,
                plan_name=None,
                current_mileage=current_mileage,
                reference_date=reference_date,
                entries=[],
            )

        sort_key = lambda e: (  # noqa: E731
            e.interval_km if e.interval_km is not None else 10**9,
            e.interval_months if e.interval_months is not None else 10**9,
        )
        entries = sorted(plan.entries, key=sort_key)
        tempario_ids = {e.tempario_id for e in entries}

        completions: dict[uuid.UUID, tuple[int, datetime | None]] = {}
        if tempario_ids:
            rows = await self.db.execute(
                select(ServiceOrderTask, ServiceOrder.sequence_number, ServiceOrder.closed_at)
                .join(ServiceOrder, ServiceOrder.id == ServiceOrderTask.service_order_id)
                .where(
                    ServiceOrder.vehicle_id == vehicle.id,
                    ServiceOrderTask.tempario_id.in_(tempario_ids),
                    ServiceOrderTask.status == TaskStatus.COMPLETADA,
                )
                .order_by(ServiceOrderTask.created_at.desc())
            )
            for task, sequence_number, closed_at in rows.all():
                # Keep the most recent completion per tempario.
                completions.setdefault(task.tempario_id, (sequence_number, closed_at))

        today = date.today()
        results: list[VehiclePlanEntryStatusRead] = []
        cumplido_flags = [entry.tempario_id in completions for entry in entries]

        for i, entry in enumerate(entries):
            completion = completions.get(entry.tempario_id)
            if cumplido_flags[i]:
                status = MaintenancePlanEntryStatus.CUMPLIDO
            elif any(cumplido_flags[i + 1 :]):
                status = MaintenancePlanEntryStatus.OMITIDO
            else:
                overdue_km = (
                    entry.interval_km is not None
                    and current_mileage is not None
                    and current_mileage >= entry.interval_km
                )
                overdue_date = entry.interval_months is not None and today >= _add_months(
                    reference_date, entry.interval_months
                )
                status = (
                    MaintenancePlanEntryStatus.VENCIDO
                    if (overdue_km or overdue_date)
                    else MaintenancePlanEntryStatus.PENDIENTE
                )

            results.append(
                VehiclePlanEntryStatusRead(
                    entry_id=entry.id,
                    tempario_id=entry.tempario_id,
                    tempario_code=entry.tempario.code,
                    tempario_name=entry.tempario.name,
                    interval_km=entry.interval_km,
                    interval_months=entry.interval_months,
                    status=status,
                    completed_at=completion[1] if completion else None,
                    completed_service_order_code=(
                        f"ODS-{completion[0]}" if completion else None
                    ),
                )
            )

        return VehiclePlanStatusRead(
            vehicle_id=vehicle.id,
            plan_id=plan.id,
            plan_brand=plan.brand,
            plan_name=plan.name,
            current_mileage=current_mileage,
            reference_date=reference_date,
            entries=results,
        )