import uuid

ADVISOR_ID = uuid.UUID("908c4a72-9ee9-4dfa-97ed-e9002492fdeb")


def add_sales_advisor(session, filial_id, advisor_id=ADVISOR_ID):
    from sqlalchemy import select
    from app.modules.roles.models import Role, RoleModulePermission
    from app.modules.roles.enums import RoleScope, AccessLevel
    from app.modules.users.models import User
    from app.modules.users.enums import UserStatus

    role = session.scalar(select(Role).where(Role.slug == "vendedor"))
    if role is None:
        role = Role(name="Vendedor", slug="vendedor", scope=RoleScope.FILIAL)
        session.add(role)
        session.flush()
        session.add(
            RoleModulePermission(
                role_id=role.id, module_id="concesionario", access=AccessLevel.EDITAR
            )
        )
    session.add(
        User(
            id=advisor_id,
            full_name="Asesor de vehículos",
            email=f"{advisor_id}@test.com",
            role_id=role.id,
            filial_id=filial_id,
            status=UserStatus.ACTIVO,
        )
    )
    session.commit()
    return advisor_id
