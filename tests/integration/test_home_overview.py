"""Home uses the caller's branch and hides unauthorized aggregates."""

import pytest
from test_client_leads import lead_env, lead_payload  # noqa: F401

from app.modules.auth.dependencies import get_current_user
from app.modules.kpis.home import router


@pytest.mark.asyncio
async def test_home_overview_permissions_and_current_branch(lead_env):
    e = lead_env
    e.app.include_router(router, prefix="/api/v1")
    created = await e.http.post("/api/v1/clients/leads", json=lead_payload(e))
    assert created.status_code == 201
    found = await e.http.get("/api/v1/dashboard/search", params={"q": "Nuevo prospecto"})
    assert found.status_code == 200 and found.json()[0]["type"] == "Cliente"
    assert "search=23456789" in found.json()[0]["href"]
    assert (await e.http.get("/api/v1/dashboard/search", params={"q": "9" * 80})).status_code == 200
    assert (await e.http.get("/api/v1/dashboard/search", params={"q": "9" * 81})).status_code == 422
    response = await e.http.get("/api/v1/dashboard/overview")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["filial_name"] == e.filial.name
    assert data["workshop"] is not None
    assert data["commercial"] is not None
    # Arbitrary filial query parameters cannot switch the authenticated scope.
    other = await e.http.get(
        "/api/v1/dashboard/overview?filial_id=00000000-0000-0000-0000-000000000001"
    )
    assert other.json()["filial_name"] == e.filial.name
    e.app.dependency_overrides[get_current_user] = lambda: e.current.model_copy(
        update={"role_slug": "no-access"}
    )
    # Role is still admin in the DB; use a role without catalog permissions.
    import uuid

    from app.modules.roles.enums import RoleScope
    from app.modules.roles.models import Role

    role = Role(name="No modules", slug=uuid.uuid4().hex, scope=RoleScope.FILIAL)
    e.db.add(role)
    await e.db.commit()
    restricted = e.current.model_copy(update={"role_slug": role.slug, "role_id": role.id})
    e.app.dependency_overrides[get_current_user] = lambda: restricted
    response = await e.http.get("/api/v1/dashboard/overview")
    assert response.status_code == 200
    assert all(response.json()[key] is None for key in ("workshop", "commercial", "clients"))

    assert (
        await e.http.get("/api/v1/dashboard/search", params={"q": "Nuevo prospecto"})
    ).json() == []


@pytest.mark.asyncio
async def test_sales_rules_are_scoped_configurable_and_validated(lead_env):
    from app.modules.ajustes.router import router as settings_router
    from app.modules.concesionario.opportunity_router import router as opportunities_router

    e = lead_env
    e.app.include_router(settings_router, prefix="/api/v1")
    e.app.include_router(opportunities_router, prefix="/api/v1")
    path = f"/api/v1/settings?filial_id={e.filial.id}"
    response = await e.http.patch(
        path,
        json={
            "vehicle_quote_valid_days": 20,
            "vehicle_min_deposit_percentage": 15,
            "vehicle_reservation_days": 5,
            "vehicle_withdrawal_retention_percentage": 8,
        },
    )
    assert response.status_code == 200, response.text
    rules = (
        await e.http.get(f"/api/v1/vehicle-opportunities/rules/current?filial_id={e.filial.id}")
    ).json()
    assert rules["vehicle_quote_valid_days"] == 20
    assert rules["vehicle_min_deposit_percentage"] == 15
    assert rules["vehicle_reservation_days"] == 5
    assert rules["vehicle_withdrawal_retention_percentage"] == 8
    assert (await e.http.patch(path, json={"vehicle_reservation_days": 0})).status_code == 422
    assert (
        await e.http.patch(path, json={"vehicle_discount_without_approval_percentage": 101})
    ).status_code == 422
    import uuid

    assert (
        await e.http.get(f"/api/v1/vehicle-opportunities/rules/current?filial_id={uuid.uuid4()}")
    ).status_code == 403
