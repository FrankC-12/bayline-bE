import asyncio

from sqlalchemy import select

import app.core.models_registry  # noqa: F401 (registers every model for SQLAlchemy)
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.modules.roles.models import Role
from app.modules.users.enums import UserStatus
from app.modules.users.models import User


async def seed_platform_user() -> None:
    settings = get_settings()
    email = settings.platform_admin_email
    password = settings.platform_admin_password

    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none() is not None:
            print(f"Platform user '{email}' already exists. Skipping.")
            return

        role_result = await session.execute(select(Role).where(Role.slug == "platform"))
        platform_role = role_result.scalar_one_or_none()
        if platform_role is None:
            raise RuntimeError("The 'platform' role was not found. Run seed_roles.py first.")

        user = User(
            full_name="Platform Admin",
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
