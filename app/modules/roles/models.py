import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.roles.enums import AccessLevel, RoleScope

# Shared Postgres enum type, reused by UserModulePermission so Alembic
# doesn't try to create the "access_level" type twice.
access_level_pg_enum = Enum(AccessLevel, name="access_level")


class Role(Base):
    """A role template. Platform and Holding scopes carry no module permissions;
    Filial-scoped roles define what they can view or edit per module."""

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scope: Mapped[RoleScope] = mapped_column(Enum(RoleScope, name="role_scope"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    permissions: Mapped[list["RoleModulePermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )


class RoleModulePermission(Base):
    """A single module-level permission owned by a role. Only present for filial-scoped roles."""

    __tablename__ = "role_module_permissions"
    __table_args__ = (UniqueConstraint("role_id", "module_id", name="uq_role_module"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    module_id: Mapped[str] = mapped_column(String(50), nullable=False)
    access: Mapped[AccessLevel] = mapped_column(access_level_pg_enum, nullable=False)

    role: Mapped["Role"] = relationship(back_populates="permissions")
