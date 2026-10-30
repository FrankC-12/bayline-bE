"""Lifecycle follows actual operations, never a client-supplied conversion flag."""

import re
from sqlalchemy import select, and_, or_
from app.core.exceptions import BadRequestError
from app.modules.clients.models import Client, Vehicle
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.parts.models import PartSale
from app.modules.parts.enums import PartSaleStatus
from app.modules.concesionario.models import VehicleSale


def lead_predicate():
    workshop = (
        select(ServiceOrder.id)
        .join(Vehicle, Vehicle.id == ServiceOrder.vehicle_id)
        .where(
            Vehicle.client_id == Client.id,
            ServiceOrder.filial_id == Client.filial_id,
            ServiceOrder.is_draft.is_(False),
            ServiceOrder.status != ServiceOrderStatus.CANCELADO,
        )
        .correlate(Client)
        .exists()
    )
    parts = (
        select(PartSale.id)
        .where(
            PartSale.client_id == Client.id,
            PartSale.filial_id == Client.filial_id,
            PartSale.status != PartSaleStatus.CANCELADO,
        )
        .correlate(Client)
        .exists()
    )
    vehicles = (
        select(VehicleSale.id)
        .where(VehicleSale.client_id == Client.id, VehicleSale.filial_id == Client.filial_id)
        .correlate(Client)
        .exists()
    )
    return and_(
        ~or_(workshop, parts, vehicles),
        Client.is_holding_billing.is_(False),
        Client.linked_supplier_id.is_(None),
    )


async def attach_lifecycle(db, clients):
    if not clients:
        return
    from sqlalchemy import and_
    from app.modules.users.models import User

    rows = (
        await db.execute(
            select(Client.id, lead_predicate(), User.full_name)
            .outerjoin(
                User,
                and_(User.id == Client.lead_assigned_user_id, User.filial_id == Client.filial_id),
            )
            .where(Client.id.in_([client.id for client in clients]))
        )
    ).all()
    states = {row[0]: (row[1], row[2]) for row in rows}
    for client in clients:
        state, name = states.get(client.id, (False, None))
        client.is_lead = bool(state)
        client.lead_assigned_user_name = name


async def resolve_sale_client(db, filial_id, client_id, document):
    if client_id:
        client = await db.get(Client, client_id)
        if client is None or client.filial_id != filial_id:
            raise BadRequestError("El cliente no pertenece a esta filial.")
        return client
    if document:
        digits = re.sub(r"\D", "", document)
        if digits:
            return (
                await db.execute(
                    select(Client).where(
                        Client.filial_id == filial_id, Client.document_number == digits
                    )
                )
            ).scalar_one_or_none()
    return None


async def link_unassigned_sales(db, client):
    """Reconcile older free-text sales when their document gets registered later.
    Existing links and immutable customer/tax snapshots are never overwritten.
    This runs once at registration instead of normalizing history on every read.
    """
    from sqlalchemy import func, update

    for model in (PartSale, VehicleSale):
        normalized = func.upper(func.coalesce(model.client_document, ""))
        for character in ("V", "E", "J", "G", "-", ".", " ", "/", "(", ")"):
            normalized = func.replace(normalized, character, "")
        await db.execute(
            update(model)
            .where(
                model.client_id.is_(None),
                model.filial_id == client.filial_id,
                normalized == client.document_number,
            )
            .values(client_id=client.id).execution_options(synchronize_session="fetch")
        )
