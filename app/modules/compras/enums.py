import enum


class VehiclePurchaseOrderStatus(str, enum.Enum):
    ENVIADA = "enviada"
    PARCIALMENTE_RECIBIDA = "parcialmente_recibida"
    RECIBIDA = "recibida"
    CONCILIADA = "conciliada"
    CANCELADA = "cancelada"
