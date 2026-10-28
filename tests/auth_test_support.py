"""Account lookup double for cookie/audit tests that do not use a database."""

import uuid
from types import SimpleNamespace
from app.modules.roles.enums import RoleScope
from app.modules.users.enums import UserStatus


def account_database(claims):
    account = SimpleNamespace(
        id=uuid.UUID(claims["sub"]),
        email=claims["email"],
        full_name="Test user",
        role_id=uuid.UUID(claims["role_id"]),
        holding_id=uuid.UUID(claims["holding_id"]) if claims.get("holding_id") else None,
        filial_id=uuid.UUID(claims["filial_id"]) if claims.get("filial_id") else None,
        status=UserStatus.ACTIVO,
        session_version=0,
    )
    role = SimpleNamespace(
        id=account.role_id, slug=claims["role_slug"], scope=RoleScope(claims["scope"])
    )

    class Database:
        async def execute(self, statement):
            return SimpleNamespace(one_or_none=lambda: (account, role))

    return Database()
