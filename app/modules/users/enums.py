import enum


class UserStatus(str, enum.Enum):
    """Lifecycle status of a user account."""

    ACTIVO = "activo"
    INVITADO = "invitado"
    INACTIVO = "inactivo"
