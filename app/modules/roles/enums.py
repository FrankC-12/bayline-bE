import enum


class RoleScope(str, enum.Enum):
    """Defines at which level of the tenant hierarchy a role operates."""

    PLATFORM = "platform"
    HOLDING = "holding"
    FILIAL = "filial"


class AccessLevel(str, enum.Enum):
    """Access level a role grants over a specific module. Absence of a row means no access.

    SIN_ACCESO is reserved for UserModulePermission overrides only — it's how
    an individual user's access is explicitly revoked below what their role
    would otherwise grant. A RoleModulePermission may never use it: "no
    access" at the role level stays represented by the row being absent."""

    VER = "ver"
    EDITAR = "editar"
    SIN_ACCESO = "sin_acceso"
