"""Counter/warehouse handoff notifications, committed with the business action."""

from sqlalchemy import and_, func, or_, select

from app.modules.notifications.models import Notification
from app.modules.roles.enums import AccessLevel
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.users.enums import UserStatus
from app.modules.users.models import User, UserModulePermission


async def queue_counter_event(db, sale, kind):
    module = "almacen" if kind == "counter_waiting" else "repuestos"
    users = (
        (
            await db.execute(
                select(User.id)
                .join(Role, Role.id == User.role_id)
                .outerjoin(
                    RoleModulePermission,
                    and_(
                        RoleModulePermission.role_id == Role.id,
                        RoleModulePermission.module_id == module,
                    ),
                )
                .outerjoin(
                    UserModulePermission,
                    and_(
                        UserModulePermission.user_id == User.id,
                        UserModulePermission.module_id == module,
                    ),
                )
                .where(
                    User.filial_id == sale.filial_id,
                    User.status == UserStatus.ACTIVO,
                    or_(
                        Role.slug == "filial-admin",
                        func.coalesce(UserModulePermission.access, RoleModulePermission.access)
                        == AccessLevel.EDITAR,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    recipients = set(users)
    if sale.created_by_user_id:
        recipients.add(sale.created_by_user_id)
    for user_id in recipients:
        user = await db.get(User, user_id)
        if user is None or user.status != UserStatus.ACTIVO or user.filial_id != sale.filial_id:
            continue
        if kind == "counter_ready":
            role = await db.get(Role, user.role_id)
            if role and (
                role.slug == "almacenista"
                or (user.id == sale.dispatched_by_user_id and role.slug != "filial-admin")
            ):
                continue
        if kind == "counter_waiting":
            title = "Esperando despacho de almacén"
            href = (
                f"/dashboard/almacen/mostrador/{sale.id}"
                if user_id in users
                else f"/dashboard/repuestos/ventas/{sale.id}"
            )
        else:
            title = "Repuestos despachados: confirma recepción en mostrador"
            href = f"/dashboard/repuestos/ventas/{sale.id}"
        key = f"{kind}:{sale.id}"
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
                    title=title,
                    body=f"{sale.code}: {title.lower()}.",
                    data={
                        "part_sale_id": str(sale.id),
                        "filial_id": str(sale.filial_id),
                        "event": kind,
                        "href": href,
                    },
                )
            )
    await db.flush()
