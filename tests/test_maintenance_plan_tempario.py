"""'Cargar solo el trabajo del plan' — the advisor flags a pending plan
Tempario on the vehicle at ODS close time; ClientService denormalizes its
code/name onto the Vehicle read model so a new ODS can offer to load it."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

from app.core.database import Base
from app.core.exceptions import BadRequestError
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.clients.service import ClientService
from app.modules.post_ventas.enums import TemparioCategory
from app.modules.post_ventas.models import Tempario
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.service import ServiceOrderService


@pytest.fixture
def workshop():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        other_filial_id = uuid.uuid4()

        client = Client(
            filial_id=filial_id,
            full_name="Cliente Uno",
            client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V,
            document_number="AB123CD",
            phone_primary="04141234567",
            address="Av. Principal",
        )
        session.add(client)
        session.flush()
        vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Corolla", plate="AB123CD")
        session.add(vehicle)
        session.flush()

        tempario = Tempario(
            filial_id=filial_id,
            category=TemparioCategory.MANTENIMIENTO_PREVENTIVO,
            sequence_number=501,
            name="Cambio de aceite y filtro",
            estimated_hours=1,
        )
        foreign_tempario = Tempario(
            filial_id=other_filial_id,
            category=TemparioCategory.MANTENIMIENTO_PREVENTIVO,
            sequence_number=1,
            name="De otra filial",
            estimated_hours=1,
        )
        session.add_all([tempario, foreign_tempario])
        session.flush()

        order = ServiceOrder(
            filial_id=filial_id,
            vehicle_id=vehicle.id,
            sequence_number=1,
            status=ServiceOrderStatus.COMPLETADO,
            invoiced_at=datetime.now(UTC),
        )
        session.add(order)
        session.commit()
        yield session, order, vehicle, tempario, foreign_tempario
    engine.dispose()


@pytest.mark.asyncio
async def test_close_order_sets_vehicle_next_maintenance_tempario_id(workshop):
    session, order, vehicle, tempario, _foreign = workshop
    service = ServiceOrderService(AsyncAdapter(session))

    await service.close_order(order.id, None, tempario.id)

    session.refresh(vehicle)
    assert vehicle.next_maintenance_tempario_id == tempario.id


@pytest.mark.asyncio
async def test_close_order_rejects_tempario_from_another_filial(workshop):
    session, order, vehicle, _tempario, foreign_tempario = workshop
    service = ServiceOrderService(AsyncAdapter(session))

    with pytest.raises(BadRequestError):
        await service.close_order(order.id, None, foreign_tempario.id)

    session.refresh(order)
    session.refresh(vehicle)
    assert order.status == ServiceOrderStatus.COMPLETADO
    assert vehicle.next_maintenance_tempario_id is None


@pytest.mark.asyncio
async def test_close_order_rejects_nonexistent_tempario(workshop):
    session, order, _vehicle, _tempario, _foreign = workshop
    service = ServiceOrderService(AsyncAdapter(session))

    with pytest.raises(BadRequestError):
        await service.close_order(order.id, None, uuid.uuid4())

    session.refresh(order)
    assert order.status == ServiceOrderStatus.COMPLETADO


@pytest.mark.asyncio
async def test_client_read_denormalizes_next_maintenance_tempario(workshop):
    session, order, vehicle, tempario, _foreign = workshop
    service = ServiceOrderService(AsyncAdapter(session))
    await service.close_order(order.id, None, tempario.id)

    client_service = ClientService(AsyncAdapter(session))
    client = await client_service.get_client(vehicle.client_id)

    loaded = client.vehicles[0]
    assert loaded.next_maintenance_tempario_id == tempario.id
    assert loaded.next_maintenance_tempario_code == "MP-501"
    assert loaded.next_maintenance_tempario_name == "Cambio de aceite y filtro"


@pytest.mark.asyncio
async def test_client_read_next_maintenance_tempario_is_none_when_unset(workshop):
    session, _order, vehicle, _tempario, _foreign = workshop
    client_service = ClientService(AsyncAdapter(session))

    client = await client_service.get_client(vehicle.client_id)

    loaded = client.vehicles[0]
    assert loaded.next_maintenance_tempario_id is None
    assert loaded.next_maintenance_tempario_code is None
    assert loaded.next_maintenance_tempario_name is None
