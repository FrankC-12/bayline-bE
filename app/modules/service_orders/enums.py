import enum


class ServiceOrderStatus(str, enum.Enum):
    PENDIENTE = "pendiente"
    EN_PROGRESO = "en_progreso"
    COMPLETADO = "completado"
    ORDEN_CERRADA = "orden_cerrada"
    CANCELADO = "cancelado"


class ServiceOrderType(str, enum.Enum):
    REGULAR = "regular"
    # No Garantías/MPT module or screen exists yet — the frontend's "Nueva
    # ODS" form deliberately hides this option so it can't be picked with
    # no workflow behind it. Kept in the API/enum for when that module ships.
    MPT = "mpt"


class TaskStatus(str, enum.Enum):
    PENDIENTE = "pendiente"
    COMPLETADA = "completada"


class TransferStatus(str, enum.Enum):
    PENDIENTE = "pendiente"
    PEDIDO = "pedido"


class UpsellStatus(str, enum.Enum):
    PENDIENTE = "pendiente"
    APROBADO = "aprobado"
    POSPUESTO = "pospuesto"
    RECHAZADO = "rechazado"