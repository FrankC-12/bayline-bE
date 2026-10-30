"""Leads share the client identity and convert only after real filial operations."""

import uuid
from types import SimpleNamespace
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, func
from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.clients.models import Client, Vehicle
from app.modules.clients.router import router
from app.modules.clients.service import ClientService
from app.modules.clients.schemas import LeadCreate
from app.modules.clients.lifecycle import resolve_sale_client
from app.modules.filiales.models import Filial
from app.modules.roles.enums import RoleScope, AccessLevel
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.users.models import User
from app.modules.users.enums import UserStatus
from app.modules.parts.models import PartSale
from app.modules.parts.enums import PartSaleStatus
from app.modules.concesionario.models import DealershipVehicle, VehicleSale
from app.modules.concesionario.enums import SaleType, VehicleCondition
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.enums import ServiceOrderStatus


@pytest_asyncio.fixture
async def lead_env(billing_db):
    ctx = billing_db
    async with ctx.sessions() as db:
        filial = await db.get(Filial, ctx.filial_id)
        role = (
            await db.execute(select(Role).where(Role.slug == "filial-admin"))
        ).scalar_one_or_none()
        if role is None:
            role = Role(name="Admin", slug="filial-admin", scope=RoleScope.FILIAL)
            db.add(role)
            await db.flush()
        actor = User(
            full_name="Lead admin",
            email=f"{uuid.uuid4().hex}@test.com",
            role_id=role.id,
            filial_id=filial.id,
            holding_id=filial.holding_id,
            status=UserStatus.ACTIVO,
        )
        db.add(actor)
        await db.commit()
        current = CurrentUser(
            user_id=actor.id,
            email=actor.email,
            role_id=role.id,
            role_slug="filial-admin",
            scope=RoleScope.FILIAL,
            filial_id=filial.id,
            holding_id=filial.holding_id,
        )
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        register_exception_handlers(app)

        async def database():
            yield db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: current
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield SimpleNamespace(
                db=db, http=http, app=app, current=current, actor=actor, filial=filial, ctx=ctx
            )


def lead_payload(e, document="23456789"):
    return dict(
        filial_id=str(e.filial.id),
        full_name="Nuevo prospecto",
        client_type="particular",
        document_type="V",
        document_number=document,
        phone_primary="04141234567",
        lead_origin="taller",
        lead_interest="vehiculo",
        lead_vehicle_interest="SUV familiar",
    )


@pytest.mark.asyncio
async def test_lead_creation_duplicate_followup_and_correct_pagination(lead_env):
    e = lead_env
    before = (
        await e.db.execute(
            select(func.count())
            .select_from(ServiceOrder)
            .where(ServiceOrder.filial_id == e.filial.id)
        )
    ).scalar_one()
    response = await e.http.post("/api/v1/clients/leads", json=lead_payload(e))
    assert response.status_code == 201, response.text
    lead = response.json()
    assert lead["is_lead"] and not lead["vehicles"] and lead["address"] == ""
    assert lead["lead_origin"] == "taller" and lead["lead_interest"] == "vehiculo"
    assert lead["created_by_user_id"] == str(e.actor.id)
    after = (
        await e.db.execute(
            select(func.count())
            .select_from(ServiceOrder)
            .where(ServiceOrder.filial_id == e.filial.id)
        )
    ).scalar_one()
    assert before == after
    assert (await e.http.post("/api/v1/clients/leads", json=lead_payload(e))).status_code == 409
    response = await e.http.patch(
        f"/api/v1/clients/{lead['id']}/lead",
        json={"lead_notes": "Llamar el viernes", "lead_status": "en_seguimiento"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["lead_interest"] == "vehiculo"
    assert response.json()["lead_origin"] == "taller"
    assert response.json()["lead_notes"] == "Llamar el viernes"
    assert (
        await e.http.patch(f"/api/v1/clients/{lead['id']}/lead", json={"lead_status": None})
    ).status_code == 422
    page = (
        await e.http.get(
            "/api/v1/clients/page",
            params={"filial_id": str(e.filial.id), "lifecycle": "lead", "page_size": 1},
        )
    ).json()
    assert page["total"] == 1 and page["items"][0]["id"] == lead["id"]
    page = (
        await e.http.get(
            "/api/v1/clients/page", params={"filial_id": str(e.filial.id), "lifecycle": "cliente"}
        )
    ).json()
    assert page["total"] == 1 and page["items"][0]["id"] != lead["id"]
    response = await e.http.post(
        "/api/v1/clients/leads",
        json={**lead_payload(e, "34567890"), "filial_id": str(uuid.uuid4())},
    )
    assert response.status_code == 403
    e.actor.status = UserStatus.INACTIVO
    await e.db.commit()
    response = await e.http.post(
        "/api/v1/clients/leads",
        json={**lead_payload(e, "34567890"), "lead_assigned_user_id": str(e.actor.id)},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_draft_is_still_lead_then_workshop_order_converts_same_record(lead_env):
    e = lead_env
    service = ClientService(e.db)
    lead = await service.create_client(LeadCreate(**lead_payload(e)))
    vehicle = Vehicle(client_id=lead.id, brand="Toyota", model="Hilux")
    e.db.add(vehicle)
    await e.db.flush()
    initial = await e.db.get(ServiceOrder, e.ctx.order_id)
    order = ServiceOrder(
        filial_id=e.filial.id,
        vehicle_id=vehicle.id,
        sequence_number=2,
        order_type_id=initial.order_type_id,
        is_draft=True,
    )
    e.db.add(order)
    await e.db.commit()
    assert (await service.get_client(lead.id)).is_lead
    order.is_draft = False
    await e.db.commit()
    assert not (await service.get_client(lead.id)).is_lead
    response = await e.http.patch(
        f"/api/v1/clients/{lead.id}/lead", json={"lead_status": "descartado"}
    )
    assert response.status_code == 400
    assert (await service.get_client(lead.id)).lead_origin == "taller"


@pytest.mark.asyncio
async def test_parts_and_vehicle_purchases_convert_leads_and_preserve_document_edits(lead_env):
    e = lead_env
    service = ClientService(e.db)
    buyer = await service.create_client(LeadCreate(**lead_payload(e)))
    other = await service.create_client(LeadCreate(**lead_payload(e, "34567890")))
    sale = PartSale(
        filial_id=e.filial.id,
        client_id=buyer.id,
        client_name=buyer.full_name,
        client_document="V-23456789",
        sequence_number=1,
        status=PartSaleStatus.CANCELADO,
    )
    e.db.add(sale)
    await e.db.commit()
    assert (await service.get_client(buyer.id)).is_lead
    sale.status = PartSaleStatus.PEDIDO
    await e.db.commit()
    assert not (await service.get_client(buyer.id)).is_lead
    buyer.document_number = "45678901"
    await e.db.commit()
    assert not (await service.get_client(buyer.id)).is_lead
    resolved = await resolve_sale_client(e.db, e.filial.id, None, "V-45.678.901")
    assert resolved.id == buyer.id
    unit = DealershipVehicle(
        filial_id=e.filial.id,
        condition=VehicleCondition.NUEVO,
        brand="Toyota",
        model="Hilux",
        year=2026,
        vin="X" * 17,
        sku="TEST-LEAD",
        price_cash=10000,
        price_financed=11000,
    )
    e.db.add(unit)
    await e.db.flush()
    e.db.add(
        VehicleSale(
            filial_id=e.filial.id,
            client_id=other.id,
            vehicle_id=unit.id,
            sequence_number=1,
            client_name=other.full_name,
            sale_type=SaleType.CONTADO,
            final_price=10000,
        )
    )
    await e.db.commit()
    assert not (await service.get_client(other.id)).is_lead
    assert (await service.page_clients(e.filial.id, lifecycle="lead")).total == 0


@pytest.mark.asyncio
async def test_salesperson_can_create_leads_without_general_client_edit_permission(lead_env):
    e = lead_env
    role = (await e.db.execute(select(Role).where(Role.slug == "vendedor"))).scalar_one_or_none()
    if role is None:
        role = Role(name="Vendedor", slug="vendedor", scope=RoleScope.FILIAL)
        e.db.add(role)
        await e.db.flush()
    for module, level in [("concesionario",AccessLevel.EDITAR),("clientes-vehiculos",AccessLevel.VER)]:
        permission=(await e.db.execute(select(RoleModulePermission).where(RoleModulePermission.role_id==role.id,RoleModulePermission.module_id==module))).scalar_one_or_none()
        if permission is None: e.db.add(RoleModulePermission(role_id=role.id,module_id=module,access=level))
        else: permission.access=level
    e.actor.role_id = role.id
    await e.db.commit()
    e.current.role_slug = "vendedor"
    e.current.role_id = role.id
    payload = lead_payload(e)
    payload.pop("lead_origin")
    payload.pop("lead_interest")
    response = await e.http.post("/api/v1/clients/leads", json=payload)
    assert response.status_code == 201, response.text
    assert (
        response.json()["lead_origin"] == "walk_in"
        and response.json()["lead_interest"] == "vehiculo"
    )
    response = await e.http.post(
        "/api/v1/clients", json={**lead_payload(e, "34567890"), "address": "Caracas"}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_registration_recognizes_prior_document_only_sales(lead_env):
    e = lead_env
    sale = PartSale(
        filial_id=e.filial.id,
        sequence_number=1,
        client_name="Nombre histórico",
        client_document="V-23.456.789",
        status=PartSaleStatus.COMPLETADO,
    )
    e.db.add(sale)
    await e.db.commit()
    response = await e.http.post("/api/v1/clients/leads", json=lead_payload(e))
    assert response.status_code == 201, response.text
    assert response.json()["is_lead"] is False
    await e.db.refresh(sale)
    assert str(sale.client_id) == response.json()["id"]
    assert sale.client_name == "Nombre histórico"
