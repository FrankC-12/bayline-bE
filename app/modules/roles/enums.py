import enum


class RoleScope(str, enum.Enum):
    """Defines at which level of the tenant hierarchy a role operates."""

    PLATFORM = "platform"
    HOLDING = "holding"
    FILIAL = "filial"


class AccessLevel(str, enum.Enum):
    """Access level a role grants over a specific module. Absence of a row means no access."""

    VER = "ver"
    EDITAR = "editar"
