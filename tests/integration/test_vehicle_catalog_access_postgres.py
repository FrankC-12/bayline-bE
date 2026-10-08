"""Operational users consume the catalog configured by Ajustes."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.filiales.models import Filial
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.users.models import User, UserModulePermission
from app.modules.vehicle_catalog import router as catalog
from app.modules.vehicle_catalog.schemas import VehicleBrandCreate, VehicleModelCreate
from app.modules.vehicle_catalog.service import VehicleCatalogService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_id", ["clientes-vehiculos", "repuestos", "concesionario", "post-ventas"]
)
async def test_operational_catalog_read_without_settings_permission(billing_db, module_id):
    ctx = billing_db
    async with ctx.sessions() as db:
        filial = await db.get(Filial, ctx.filial_id)
        service = VehicleCatalogService(db)
        brand = await service.create_brand(filial.holding_id, VehicleBrandCreate(name="Toyota QA"))
        model = await service.create_model(
            brand.id, filial.holding_id, VehicleModelCreate(name="Hilux", vehicle_type="Pick-up")
        )
        inactive_model = await service.create_model(
            brand.id, filial.holding_id, VehicleModelCreate(name="Desactivado", vehicle_type="SUV")
        )
        await service.set_model_active(inactive_model.id, filial.holding_id, is_active=False)
        inactive = await service.create_brand(
            filial.holding_id, VehicleBrandCreate(name="Marca desactivada")
        )
        await service.set_brand_active(inactive.id, filial.holding_id, is_active=False)
        role = Role(name="Operador QA", slug=f"qa-{uuid.uuid4()}", scope=RoleScope.FILIAL)
        db.add(role)
        await db.flush()
        db.add(RoleModulePermission(role_id=role.id, module_id=module_id, access=AccessLevel.VER))
        user = User(
            full_name="Operador QA",
            email=f"{uuid.uuid4()}@example.com",
            role_id=role.id,
            filial_id=ctx.filial_id,
        )
        db.add(user)
        await db.commit()
        current = CurrentUser(
            user_id=user.id,
            email=user.email,
            role_id=role.id,
            role_slug=role.slug,
            scope="filial",
            filial_id=ctx.filial_id,
            holding_id=None,
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(catalog.router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: current
        app.dependency_overrides[get_db] = lambda: db
        db.expunge_all()
        path = f"/api/v1/vehicle-catalog/brands?filial_id={ctx.filial_id}"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            response = await http.get(path)
            assert response.status_code == 200, response.text
            rows = response.json()
            row = next(row for row in rows if row["name"] == "Toyota QA")
            assert any(item["id"] == str(model.id) for item in row["models"])
            assert "Desactivado" not in [item["name"] for item in row["models"]]
            assert "Marca desactivada" not in [row["name"] for row in rows]
            assert (await http.get(path + "&include_inactive=true")).status_code == 403
            assert (await http.post(path, json={"name": "No autorizado"})).status_code == 403
            assert (
                await http.patch(
                    f"/api/v1/vehicle-catalog/brands/{brand.id}?filial_id={ctx.filial_id}",
                    json={"name": "Cambio"},
                )
            ).status_code == 403
            assert (
                await http.get(f"/api/v1/vehicle-catalog/brands?filial_id={uuid.uuid4()}")
            ).status_code == 403
            db.add(
                UserModulePermission(
                    user_id=current.user_id, module_id=module_id, access=AccessLevel.SIN_ACCESO
                )
            )
            await db.commit()
            assert (await http.get(path)).status_code == 403
