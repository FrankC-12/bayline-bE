import enum


class ServiceOrderStatus(str, enum.Enum):
    PENDIENTE = "pendiente"
    EN_PROGRESO = "en_progreso"
    COMPLETADO = "completado"
    ORDEN_CERRADA = "orden_cerrada"
    CANCELADO = "cancelado"


class ServiceOrderType(str, enum.Enum):
    REGULAR = "regular"
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