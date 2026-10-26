import asyncio

from sqlalchemy import select

import app.core.models_registry  # noqa: F401 (registers every model for SQLAlchemy)
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.modules.roles.enums import RoleScope
from app.modules.roles.models import Role
from app.modules.users.enums import UserStatus
from app.modules.users.models import User


async def seed_platform_user() -> None:
    settings = get_settings()
    email = settings.platform_admin_email.strip().casefold()
    password = settings.platform_admin_password

    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(User).where(User.email == email))
        found = existing.scalar_one_or_none()
        if found is not None:
            role = await session.get(Role, found.role_id)
            if role is None or role.scope != RoleScope.PLATFORM:
                raise RuntimeError(
                    "El correo configurado ya pertenece a un usuario sin alcance de plataforma."
                )
            print("El administrador de plataforma ya existe; su contraseña no se modifica.")
            return

        if (
            len(password) < 12
            or len(password.encode("utf-8")) > 72
            or password in {"change-this-password", "REPLACE_WITH_RANDOM_ADMIN_PASSWORD"}
        ):
            raise RuntimeError(
                "Define PLATFORM_ADMIN_PASSWORD con 12 o más caracteres (máximo 72 bytes)."
            )

        role_result = await session.execute(select(Role).where(Role.slug == "platform"))
        platform_role = role_result.scalar_one_or_none()
        if platform_role is None:
            platform_role = Role(name="Motrix Platform", slug="platform", scope=RoleScope.PLATFORM)
            session.add(platform_role)
            await session.flush()

        if platform_role.scope != RoleScope.PLATFORM:
            raise RuntimeError("El rol platform no tiene alcance de plataforma.")

        user = User(
            full_name="motrixadmin",
            email=email,
            password_hash=hash_password(password),
            status=UserStatus.ACTIVO,
            role_id=platform_role.id,
        )
        session.add(user)
        await session.commit()
        print(f"Created Platform user '{email}'.")


if __name__ == "__main__":
    asyncio.run(seed_platform_user())
