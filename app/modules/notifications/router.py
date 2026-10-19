import hashlib
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.notifications.models import Device, Notification

router = APIRouter(tags=["Notifications"])


class DeviceInput(BaseModel):
    token: str = Field(min_length=20, max_length=4096)
    platform: Literal["ios", "android"]


@router.put("/devices")
async def register_device(
    payload: DeviceInput,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy.dialects.postgresql import insert

    digest = hashlib.sha256(payload.token.encode()).hexdigest()
    statement = insert(Device).values(
        id=uuid.uuid4(),
        user_id=user.user_id,
        token_hash=digest,
        token=payload.token,
        platform=payload.platform,
        enabled=True,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[Device.token_hash],
        set_={"user_id": user.user_id, "enabled": True, "platform": payload.platform},
    ).returning(Device.id)
    device_id = (await db.execute(statement)).scalar_one()
    await db.commit()
    return {"id": device_id, "platform": payload.platform, "enabled": True}


@router.delete("/devices/{device_id}", status_code=204)
async def unregister_device(
    device_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    device = await db.get(Device, device_id)
    if device is None or device.user_id != user.user_id:
        raise NotFoundError("Dispositivo no encontrado.")
    device.enabled = False
    await db.commit()


@router.get("/notifications")
async def list_notifications(
    limit: int = Query(50, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        (
            await db.execute(
                select(Notification)
                .where(Notification.user_id == user.user_id)
                .order_by(Notification.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": n.id,
            "kind": n.kind,
            "title": n.title,
            "body": n.body,
            "data": n.data,
            "created_at": n.created_at,
            "read_at": n.read_at,
        }
        for n in rows
    ]


@router.patch("/notifications/{notification_id}/read", status_code=204)
async def read_notification(
    notification_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    n = await db.get(Notification, notification_id)
    if n is None or n.user_id != user.user_id:
        raise NotFoundError("Aviso no encontrado.")
    n.read_at = n.read_at or datetime.now(UTC)
    await db.commit()
