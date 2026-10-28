"""Salespeople cannot execute warehouse work, even by directly calling the API."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.filiales.models import Filial
from app.modules.notifications.models import Notification
from app.modules.parts.models import Part, PartCategory, PartSale, PartWarranty
from app.modules.parts.router import router, get_service
from app.modules.parts.service import PartsService
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.users.enums import UserStatus
from app.modules.users.models import User
from app.modules.warehouse.models import Warehouse, PartLot


@pytest.mark.asyncio
async def test_counter_handoff_permissions_quantities_notifications_and_retries(billing_db):
    ctx = billing_db
    async with ctx.sessions() as db:
        filial = await db.get(Filial, ctx.filial_id)
        warehouse = Warehouse(filial_id=filial.id, name="Mostrador origen")
        category = PartCategory(holding_id=filial.holding_id, name="Mostrador prueba")
        db.add_all([warehouse, category])
        await db.flush()
        part = Part(
            filial_id=filial.id,
            category_id=category.id,
            name="Filtro de prueba",
            code="COUNTER",
            price=13,
            stock_quantity=20,
        )
        db.add(part)
        await db.flush()
        lot = PartLot(
            filial_id=filial.id,
            warehouse_id=warehouse.id,
            part_id=part.id,
            quantity_received=20,
            quantity_remaining=20,
            unit_cost=10,
        )
        db.add(lot)
        callers = []
        for module, access in [
            ("repuestos", AccessLevel.EDITAR),
            ("almacen", AccessLevel.EDITAR),
            ("almacen", AccessLevel.VER),
        ]:
            token = uuid.uuid4().hex
            role = Role(name=token, slug=token, scope=RoleScope.FILIAL)
            db.add(role)
            await db.flush()
            db.add(RoleModulePermission(role_id=role.id, module_id=module, access=access))
            user = User(
                full_name=module,
                email=f"{token}@test.com",
                role_id=role.id,
                filial_id=filial.id,
                status=UserStatus.ACTIVO,
            )
            db.add(user)
            await db.flush()
            callers.append(
                CurrentUser(
                    user_id=user.id,
                    email=user.email,
                    role_id=role.id,
                    role_slug=role.slug,
                    scope=RoleScope.FILIAL,
                    filial_id=filial.id,
                    holding_id=None,
                )
            )
        # Almacén's seeded role also has catalog editing; it still must not receive its own dispatch.
        db.add(
            RoleModulePermission(
                role_id=callers[1].role_id, module_id="repuestos", access=AccessLevel.EDITAR
            )
        )
        await db.commit()
        warehouse_id = warehouse.id
        part_id = part.id
        seller, warehouse_user, readonly = callers
        active = seller
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_service] = lambda: PartsService(db)
        app.dependency_overrides[get_current_user] = lambda: active
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            result = await http.post(
                "/api/v1/part-sales",
                json={
                    "filial_id": str(ctx.filial_id),
                    "warehouse_id": str(warehouse_id),
                    "client_name": "Cliente mostrador",
                    "lines": [{"part_id": str(part_id), "quantity": 2}],
                },
            )
            assert result.status_code == 201, result.text
            sale = result.json()
            path = f"/api/v1/part-sales/{sale['id']}"
            assert sale["status"] == "pendiente" and sale["dispatched_at"] is None
            assert sale["created_by_user_id"] == str(seller.user_id)
            assert sale["lines"][0]["part_name"] == "Filtro de prueba"
            dispatch = {
                "status": "pedido",
                "dispatched_lines": [{"line_id": sale["lines"][0]["id"], "dispatched_quantity": 2}],
            }
            assert (await http.patch(path, json=dispatch)).status_code == 403
            assert (await http.patch(path, json={"status": "completado"})).status_code == 400
            active = readonly
            assert (await http.get(path)).status_code == 200
            assert (await http.patch(path, json=dispatch)).status_code == 403
            active = warehouse_user.model_copy(update={"filial_id": uuid.uuid4()})
            assert (await http.get(path)).status_code == 403
            assert (await http.patch(path, json=dispatch)).status_code == 403
            active = warehouse_user
            assert (
                await http.get("/api/v1/parts", params={"filial_id": str(ctx.filial_id)})
            ).status_code == 200
            wrong = {
                "status": "pedido",
                "dispatched_lines": [{"line_id": sale["lines"][0]["id"], "dispatched_quantity": 1}],
            }
            assert (await http.patch(path, json=wrong)).status_code == 400
            current = (await http.get(path)).json()
            assert current["dispatched_at"] is None and current["status"] == "pendiente"
            result = await http.patch(path, json=dispatch)
            assert result.status_code == 200, result.text
            dispatched = result.json()
            assert dispatched["dispatched_by_user_id"] == str(warehouse_user.user_id)
            assert dispatched["dispatched_at"]
            assert (await http.patch(path, json=dispatch)).json()["dispatched_at"] == dispatched[
                "dispatched_at"
            ]
            assert (await http.patch(path, json={"status": "completado"})).status_code == 403
            active = seller
            received = (await http.patch(path, json={"status": "completado"})).json()
            assert received["status"] == "completado"
            assert received["received_by_user_id"] == str(seller.user_id)
            assert received["completed_at"]
            assert (await http.patch(path, json={"status": "completado"})).json()[
                "completed_at"
            ] == received["completed_at"]
            notices = (
                (
                    await db.execute(
                        select(Notification).where(Notification.event_key.like(f"%:{sale['id']}"))
                    )
                )
                .scalars()
                .all()
            )
            assert {(n.user_id, n.kind) for n in notices} == {
                (seller.user_id, "counter_waiting"),
                (warehouse_user.user_id, "counter_waiting"),
                (seller.user_id, "counter_ready"),
            }
            await db.refresh(lot)
            assert lot.quantity_remaining == 18
            warranties = (
                (
                    await db.execute(
                        select(PartWarranty).where(
                            PartWarranty.part_sale_line_id == uuid.UUID(sale["lines"][0]["id"])
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(warranties) == 1
            # Historical 'pedido' alone does not prove warehouse dispatched it.
            result = await http.post(
                "/api/v1/part-sales",
                json={
                    "filial_id": str(ctx.filial_id),
                    "warehouse_id": str(warehouse_id),
                    "client_name": "Caso anterior",
                    "lines": [{"part_id": str(part_id), "quantity": 1}],
                },
            )
            legacy = result.json()
            record = await db.get(PartSale, uuid.UUID(legacy["id"]))
            from app.modules.parts.enums import PartSaleStatus

            record.status = PartSaleStatus.PEDIDO
            await db.commit()
            legacy_path = f"/api/v1/part-sales/{legacy['id']}"
            assert (await http.patch(legacy_path, json={"status": "completado"})).status_code == 400
            active = warehouse_user
            confirmed = await http.patch(
                legacy_path,
                json={
                    "status": "pedido",
                    "dispatched_lines": [
                        {"line_id": legacy["lines"][0]["id"], "dispatched_quantity": 1}
                    ],
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["dispatched_at"]
