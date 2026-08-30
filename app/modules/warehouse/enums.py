import enum


class MovementType(str, enum.Enum):
    ENTRADA = "entrada"
    SALIDA = "salida"
    TRANSFERENCIA_SALIDA = "transferencia_salida"
    TRANSFERENCIA_ENTRADA = "transferencia_entrada"
    DEVOLUCION = "devolucion"


class MovementReason(str, enum.Enum):
    CONSUMO_ODS = "consumo_ods"
    AJUSTE_INVENTARIO = "ajuste_inventario"
    DEVOLUCION_PROVEEDOR = "devolucion_proveedor"
    OTRO = "otro"


class TransferStatus(str, enum.Enum):
    PEDIDO = "pedido"
    EN_PROCESO = "en_proceso"
    COMPLETADA = "completada"
    CANCELADA = "cancelada"