"""Compact home aggregates, scoped to the caller's branch and module permissions."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.venezuela_time import venezuela_today
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.exceptions import InsufficientPermissionsError
from app.modules.auth.schemas import CurrentUser
from app.modules.clients.models import Client
from app.modules.concesionario.models import DealershipVehicle, VehicleSale
from app.modules.concesionario.opportunities import stage_sql
from app.modules.concesionario.opportunity_models import VehicleOpportunity
from app.modules.filiales.models import Filial
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.models import ServiceOrder

router = APIRouter(tags=["Inicio"])


@router.get("/dashboard/overview")
async def overview(
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if not user.filial_id:
        raise InsufficientPermissionsError()
    filial = await db.get(Filial, user.filial_id)
    result = {
        "filial_name": filial.name if filial else "Mi sucursal",
        "as_of": datetime.now(ZoneInfo("America/Caracas")),
        "workshop": None,
        "commercial": None,
        "clients": None,
    }

    async def allowed(module):
        try:
            await ensure_module_access(db, user, user.filial_id, module, AccessLevel.VER)
            return True
        except InsufficientPermissionsError:
            return False

    start = datetime.combine(
        venezuela_today().replace(day=1), time.min, ZoneInfo("America/Caracas")
    )
    if await allowed("asesor-servicios"):
        counts = dict(
            (
                await db.execute(
                    select(ServiceOrder.status, func.count())
                    .where(
                        ServiceOrder.filial_id == user.filial_id,
                        ServiceOrder.is_draft.is_(False),
                        ServiceOrder.status.notin_(
                            [ServiceOrderStatus.ORDEN_CERRADA, ServiceOrderStatus.CANCELADO]
                        ),
                    )
                    .group_by(ServiceOrder.status)
                )
            ).all()
        )
        drafts = await db.scalar(
            select(func.count())
            .select_from(ServiceOrder)
            .where(
                ServiceOrder.filial_id == user.filial_id,
                ServiceOrder.is_draft.is_(True),
                ServiceOrder.status != ServiceOrderStatus.CANCELADO,
            )
        )
        result["workshop"] = {
            "counts": {key.value: value for key, value in counts.items()},
            "active": sum(counts.values()),
            "drafts": drafts,
        }
    if await allowed("concesionario"):
        stage = stage_sql()
        counts = dict(
            (
                await db.execute(
                    select(stage, func.count())
                    .select_from(VehicleOpportunity)
                    .outerjoin(
                        DealershipVehicle, DealershipVehicle.id == VehicleOpportunity.vehicle_id
                    )
                    .outerjoin(VehicleSale, VehicleSale.id == VehicleOpportunity.sale_id)
                    .where(VehicleOpportunity.filial_id == user.filial_id)
                    .group_by(stage)
                )
            ).all()
        )
        stock = dict(
            (
                await db.execute(
                    select(DealershipVehicle.status, func.count())
                    .where(DealershipVehicle.filial_id == user.filial_id)
                    .group_by(DealershipVehicle.status)
                )
            ).all()
        )
        sold = await db.scalar(
            select(func.count())
            .select_from(VehicleSale)
            .where(VehicleSale.filial_id == user.filial_id, VehicleSale.created_at >= start)
        )
        result["commercial"] = {
            "counts": counts,
            "stock": {key.value: value for key, value in stock.items()},
            "sold_month": sold,
        }
    if await allowed("clientes-vehiculos"):
        total, new = (
            await db.execute(
                select(func.count(), func.count().filter(Client.created_at >= start)).where(
                    Client.filial_id == user.filial_id
                )
            )
        ).one()
        result["clients"] = {"total": total, "new_month": new}
    return result


@router.get("/dashboard/search")
async def search(
    q: str = "", user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    from fastapi import HTTPException
    from sqlalchemy import or_

    if not user.filial_id:
        raise InsufficientPermissionsError()
    term = q.strip()
    if len(term) > 80:
        raise HTTPException(422, "La búsqueda admite hasta 80 caracteres.")
    if len(term) < 2:
        return []
    results = []

    async def allowed(module):
        try:
            await ensure_module_access(db, user, user.filial_id, module, AccessLevel.VER)
            return True
        except InsufficientPermissionsError:
            return False

    if await allowed("clientes-vehiculos"):
        clients = (
            await db.execute(
                select(Client)
                .where(
                    Client.filial_id == user.filial_id,
                    or_(
                        func.lower(Client.full_name).contains(term.lower(), autoescape=True),
                        Client.document_number.contains(term, autoescape=True),
                    ),
                )
                .order_by(Client.full_name, Client.id)
                .limit(5)
            )
        ).scalars()
        from urllib.parse import urlencode

        results.extend(
            {
                "label": c.full_name,
                "type": "Cliente",
                "href": "/dashboard/clientes?" + urlencode({"search": c.document_number}),
            }
            for c in clients
        )
    if await allowed("concesionario"):
        units = (
            await db.execute(
                select(DealershipVehicle)
                .where(
                    DealershipVehicle.filial_id == user.filial_id,
                    or_(
                        *(
                            func.lower(col).contains(term.lower(), autoescape=True)
                            for col in (
                                DealershipVehicle.brand,
                                DealershipVehicle.model,
                                DealershipVehicle.plate,
                                DealershipVehicle.vin,
                                DealershipVehicle.sku,
                            )
                        )
                    ),
                )
                .order_by(DealershipVehicle.created_at.desc(), DealershipVehicle.id)
                .limit(5)
            )
        ).scalars()
        from urllib.parse import urlencode

        results.extend(
            {
                "label": f"{u.brand} {u.model} · {u.plate or u.vin}",
                "type": "Vehículo",
                "href": "/dashboard/concesionario?" + urlencode({"search": u.vin}),
            }
            for u in units
        )
    if await allowed("asesor-servicios"):
        number = term.upper().removeprefix("ODS-")
        if number.isascii() and number.isdigit() and int(number) <= 2147483647:
            orders = (
                await db.execute(
                    select(ServiceOrder)
                    .where(
                        ServiceOrder.filial_id == user.filial_id,
                        ServiceOrder.sequence_number == int(number),
                    )
                    .limit(5)
                )
            ).scalars()
            results.extend(
                {
                    "label": f"ODS-{o.sequence_number}",
                    "type": "Orden de servicio",
                    "href": f"/dashboard/servicios/{o.id}",
                }
                for o in orders
            )
    return results
