"""Transactional outbox; FCM failure must never roll back workshop work."""

import asyncio
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from sqlalchemy import or_, select

from app.modules.notifications.models import Device, Notification
from app.modules.users.enums import UserStatus
from app.modules.users.models import User

TITLES = {
    "tecnico_asignado": "Nueva orden asignada",
    "repuestos_listos": "Repuestos listos para retirar",
    "upsell_identificado": "Nuevo hallazgo del técnico",
    "upsell_aprobado": "Hallazgo aprobado",
    "upsell_rechazado": "Hallazgo rechazado",
    "upsell_pospuesto": "Hallazgo pospuesto",
    "tarea_cerrada": "Tarea finalizada",
    "orden_lista": "Orden lista",
}


async def queue_order_event(db, order, kind, entity_id, recipients=None):
    recipients = recipients if recipients is not None else [order.advisor_user_id]
    for user_id in set(recipients) - {None}:
        user = await db.get(User, user_id)
        if (
            user is None
            or user.status != UserStatus.ACTIVO
            or (user.filial_id is not None and user.filial_id != order.filial_id)
        ):
            continue
        key = f"{kind}:{entity_id}"
        existing = (
            await db.execute(
                select(Notification.id).where(
                    Notification.user_id == user_id, Notification.event_key == key
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                Notification(
                    user_id=user_id,
                    event_key=key,
                    kind=kind,
                    title=TITLES[kind],
                    body=f"{order.code}: {TITLES[kind].lower()}.",
                    data={
                        "service_order_id": str(order.id),
                        "event": kind,
                        "entity_id": str(entity_id),
                        "filial_id": str(order.filial_id),
                    },
                )
            )
            await db.flush()


@lru_cache
def firebase_app():
    import firebase_admin
    from firebase_admin import credentials

    from app.core.config import get_settings

    settings = get_settings()
    credential = (
        credentials.Certificate(settings.firebase_credentials_file)
        if settings.firebase_credentials_file
        else credentials.ApplicationDefault()
    )
    return firebase_admin.initialize_app(
        credential,
        {"projectId": settings.firebase_project_id} if settings.firebase_project_id else None,
        name="bayline-push",
    )


def build_message(device, notification):
    from firebase_admin import messaging

    return messaging.Message(
        token=device.token,
        notification=messaging.Notification(title=notification.title, body=notification.body),
        data={**notification.data, "notification_id": str(notification.id)},
        # APNs plays no sound unless the payload asks for one.
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(aps=messaging.Aps(sound="default"))
        ),
    )


def send_fcm(device, notification):
    from firebase_admin import messaging

    return messaging.send(build_message(device, notification), app=firebase_app())


async def deliver_batch(db, sender=send_fcm):
    now = datetime.now(UTC)
    rows = (
        (
            await db.execute(
                select(Notification)
                .where(
                    Notification.sent_at.is_(None),
                    Notification.attempts < 8,
                    or_(
                        Notification.next_attempt_at.is_(None), Notification.next_attempt_at <= now
                    ),
                )
                .order_by(Notification.created_at)
                .limit(50)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for notification in rows:
        user = await db.get(User, notification.user_id)
        if (
            user is None
            or user.status != UserStatus.ACTIVO
            or (
                user.filial_id
                and notification.data.get("filial_id")
                and str(user.filial_id) != notification.data["filial_id"]
            )
        ):
            notification.sent_at = now
            continue
        devices = (
            (
                await db.execute(
                    select(Device).where(
                        Device.user_id == notification.user_id, Device.enabled.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
        delivered = set(notification.delivered_devices or [])
        failed = False
        for device in devices:
            if str(device.id) in delivered:
                continue
            try:
                await asyncio.to_thread(sender, device, notification)
                delivered.add(str(device.id))
            except Exception as exc:
                # Do not log SDK exceptions: their text may include device tokens.
                if type(exc).__name__ in {"UnregisteredError", "SenderIdMismatchError"}:
                    device.enabled = False
                else:
                    failed = True
        notification.delivered_devices = sorted(delivered)
        notification.attempts += 1
        if not failed:
            notification.sent_at = now  # No devices: inbox still holds the event.
        else:
            notification.next_attempt_at = now + timedelta(
                seconds=min(3600, 15 * 2**notification.attempts)
            )
    await db.commit()
    return len(rows)


async def worker():
    from app.core.config import get_settings
    from app.core.database import AsyncSessionLocal

    if not get_settings().push_enabled:
        return
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await deliver_batch(db)
        except Exception:
            import logging

            logging.getLogger(__name__).warning("Push delivery batch failed; retrying later")
        await asyncio.sleep(10)
