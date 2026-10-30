"""Pipeline follows real quotations, reservations, sales and confirmed deliveries."""

import uuid
from datetime import timedelta
import pytest
from sqlalchemy import select
from test_client_leads import lead_env, lead_payload
from app.core.venezuela_time import venezuela_today
from app.modules.concesionario.router import router as dealership_router
from app.modules.concesionario.opportunity_router import router as opportunities_router
from app.modules.concesionario.models import DealershipVehicle
from app.modules.concesionario.enums import VehicleCondition, VehicleStatus
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.roles.enums import RoleScope, AccessLevel
from app.modules.users.models import User
from app.modules.users.enums import UserStatus


@pytest.mark.asyncio
async def test_legacy_opportunity_full_operational_pipeline(lead_env):
    e = lead_env
    e.app.include_router(dealership_router, prefix="/api/v1")
    e.app.include_router(opportunities_router, prefix="/api/v1")
    role = (await e.db.execute(select(Role).where(Role.slug == "vendedor"))).scalar_one_or_none()
    if role is None:
        role = Role(name="Vendedor", slug="vendedor", scope=RoleScope.FILIAL)
        e.db.add(role)
        await e.db.flush()
    permission = (
        await e.db.execute(
            select(RoleModulePermission).where(
                RoleModulePermission.role_id == role.id,
                RoleModulePermission.module_id == "concesionario",
            )
        )
    ).scalar_one_or_none()
    if permission is None:
        e.db.add(
            RoleModulePermission(
                role_id=role.id, module_id="concesionario", access=AccessLevel.EDITAR
            )
        )
    else:
        permission.access = AccessLevel.EDITAR
    seller = User(
        full_name="Vendedor de prueba",
        email=f"{uuid.uuid4().hex}@example.com",
        role_id=role.id,
        filial_id=e.filial.id,
        status=UserStatus.ACTIVO,
    )
    unit = DealershipVehicle(
        filial_id=e.filial.id,
        condition=VehicleCondition.NUEVO,
        status=VehicleStatus.DISPONIBLE,
        brand="Toyota",
        model="Hilux",
        year=2026,
        vin="X" * 17,
        sku="OP-TEST",
        price_cash=10000,
        price_financed=11000,
        cost_price=8000,
        price_currency="USD",
        iva_percentage=16,
        luxury_tax_percentage=0,
        igtf_percentage=3,
    )
    e.db.add_all([seller, unit])
    await e.db.commit()
    result = await e.http.post("/api/v1/clients/leads", json=lead_payload(e))
    assert result.status_code == 201, result.text
    client_id = result.json()["id"]

    async def board():
        response = await e.http.get(
            "/api/v1/vehicle-opportunities", params={"filial_id": str(e.filial.id)}
        )
        assert response.status_code == 200, response.text
        return response.json()

    data = await board()
    assert data["counts"]["prospecto"] == 1 and data["total"] == 1
    opp = data["items"][0]
    from app.modules.concesionario.opportunity_models import VehicleOpportunity
    legacy = await e.db.get(VehicleOpportunity, uuid.UUID(opp["id"]))
    legacy.workflow_version = 0
    await e.db.commit()
    path = f"/api/v1/vehicle-opportunities/{opp['id']}"
    result = await e.http.post(
        path + "/deliver", json={"confirmed": True, "reference": "Recibido por cliente"}
    )
    assert result.status_code == 400
    result = await e.http.post(
        path + "/quote",
        json={
            "expected_version": opp["version"],
            "vehicle_id": str(unit.id),
            "sale_type": "contado",
            "amount": 1,
        },
    )
    assert result.status_code == 200, result.text
    opp = result.json()
    assert opp["stage"] == "cotizada" and opp["amount"] == 11600
    assert opp["quote_expires_at"] == (venezuela_today() + timedelta(days=15)).isoformat()
    rejected_discount = await e.http.post(
        path + "/quote",
        json={
            "expected_version": opp["version"],
            "vehicle_id": str(unit.id),
            "discount_percentage": 4,
        },
    )
    assert rejected_discount.status_code == 400

    stale = await e.http.post(
        path + "/quote",
        json={"expected_version": 1, "vehicle_id": str(unit.id), "sale_type": "contado"},
    )
    assert stale.status_code == 409
    reservation = {
        "client_id": client_id,
        "advisor_user_id": str(seller.id),
        "deposit_amount": 1160,
        "expires_at": (venezuela_today() + timedelta(days=7)).isoformat(),
        "reason": "Reserva solicitada por cliente",
    }
    too_small = await e.http.post(
        f"/api/v1/dealership-vehicles/{unit.id}/reserve",
        json={**reservation, "deposit_amount": 100},
    )
    assert too_small.status_code == 400 and "mínimo" in too_small.json()["message"]
    await e.db.refresh(unit)
    too_long = await e.http.post(
        f"/api/v1/dealership-vehicles/{unit.id}/reserve",
        json={**reservation, "expires_at": (venezuela_today() + timedelta(days=8)).isoformat()},
    )
    assert too_long.status_code == 400
    await e.db.refresh(unit)
    await e.db.refresh(e.filial)
    await e.db.refresh(seller)
    result = await e.http.post(f"/api/v1/dealership-vehicles/{unit.id}/reserve", json=reservation)
    assert result.status_code == 200, result.text
    data = await board()
    opp = data["items"][0]
    assert data["total"] == 1 and opp["stage"] == "reservada"
    result = await e.http.patch(
        path,
        json={
            "expected_version": opp["version"],
            "stage": "perdida",
            "lost_reason": "Cliente desistió",
        },
    )
    assert result.status_code == 400
    unit.price_cash = 20000
    await e.db.commit()
    result = await e.http.patch(
        f"/api/v1/dealership-vehicles/{unit.id}",
        json={
            "status": "vendido",
            "sale": {
                "opportunity_id": opp["id"],
                "opportunity_version": opp["version"],
                "client_id": client_id,
                "client_name": "Nombre enviado",
                "advisor_user_id": str(seller.id),
                "sale_type": "contado",
                "payment_method": "bs",
                "usd_base": 0,
                "bs_payment_channel": "transferencia",
            },
        },
    )
    assert result.status_code == 200, result.text
    data = await board()
    opp = data["items"][0]
    assert data["total"] == 1 and opp["stage"] == "facturada" and opp["sale_id"]
    assert opp["amount"] == 11600
    result = await e.http.post(
        path + "/deliver", json={"confirmed": False, "reference": "Recibido por cliente"}
    )
    assert result.status_code == 422
    result = await e.http.post(
        path + "/deliver", json={"confirmed": True, "reference": "Recibido por cliente"}
    )
    assert result.status_code == 200, result.text
    delivered = result.json()
    assert delivered["stage"] == "entregada" and delivered["delivered_at"]
    repeated = await e.http.post(
        path + "/deliver", json={"confirmed": True, "reference": "Misma entrega"}
    )
    assert repeated.json()["delivered_at"] == delivered["delivered_at"]
    assert len(repeated.json()["activity"]) == len(delivered["activity"])
    assert (await e.http.get(f"/api/v1/clients/{client_id}")).json()["is_lead"] is False
    assert (
        await e.http.get("/api/v1/vehicle-opportunities", params={"filial_id": str(uuid.uuid4())})
    ).status_code == 403


@pytest.mark.asyncio
async def test_opportunity_financing_and_loss_are_not_fake_invoices(lead_env):
    e = lead_env
    e.app.include_router(opportunities_router, prefix="/api/v1")
    await e.http.post("/api/v1/clients/leads", json=lead_payload(e))
    data = (
        await e.http.get("/api/v1/vehicle-opportunities", params={"filial_id": str(e.filial.id)})
    ).json()
    opp = data["items"][0]
    path = f"/api/v1/vehicle-opportunities/{opp['id']}"
    assert (
        await e.http.patch(path, json={"expected_version": opp["version"], "stage": "facturada"})
    ).status_code == 422
    assert (
        await e.http.patch(
            path, json={"expected_version": opp["version"], "stage": "financiamiento"}
        )
    ).status_code == 400
    unit = DealershipVehicle(
        filial_id=e.filial.id,
        condition=VehicleCondition.NUEVO,
        brand="Ford",
        model="Explorer",
        year=2026,
        vin="Y" * 17,
        sku="OP-FIN",
        price_cash=10000,
        price_financed=11000,
    )
    e.db.add(unit)
    await e.db.commit()
    result = await e.http.post(
        path + "/quote",
        json={
            "expected_version": opp["version"],
            "vehicle_id": str(unit.id),
            "sale_type": "financiado",
        },
    )
    assert result.status_code == 200, result.text
    opp = result.json()
    assert opp["amount"] == 11000
    result = await e.http.patch(
        path, json={"expected_version": opp["version"], "stage": "financiamiento"}
    )
    assert result.status_code == 200 and result.json()["stage"] == "financiamiento"
    opp = result.json()
    result = await e.http.patch(
        path,
        json={
            "expected_version": opp["version"],
            "stage": "perdida",
            "lost_reason": "No desea continuar",
        },
    )
    assert result.status_code == 200 and result.json()["stage"] == "perdida"
    opp = result.json()
    result = await e.http.patch(
        path, json={"expected_version": opp["version"], "stage": "prospecto"}
    )
    assert result.status_code == 200 and result.json()["stage"] == "cotizada"
    assert result.json()["sale_id"] is None
