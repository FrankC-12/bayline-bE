"""Account lookup double for cookie/audit tests that do not use a database."""

import uuid
from types import SimpleNamespace
from app.modules.roles.enums import RoleScope
from app.modules.users.enums import UserStatus
from app.modules.filiales.models import Filial
from app.modules.holdings.models import Holding


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
            entity = statement.column_descriptions[0].get("entity")
            item = (
                SimpleNamespace(
                    id=account.filial_id,
                    holding_id=account.holding_id or uuid.UUID(int=1),
                    is_active=True,
                )
                if entity == Filial
                else SimpleNamespace(is_active=True) if entity == Holding else None
            )
            return SimpleNamespace(
                one_or_none=lambda: (account, role), scalar_one_or_none=lambda: item
            )

    return Database()
