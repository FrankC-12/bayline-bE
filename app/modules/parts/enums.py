import enum


class PartAvailability(str, enum.Enum):
    DISPONIBLE = "disponible"
    AGOTADO = "agotado"


class PartSaleStatus(str, enum.Enum):
    PENDIENTE = "pendiente"
    PEDIDO = "pedido"
    COMPLETADO = "completado"
    CANCELADO = "cancelado"


class ReturnCondition(str, enum.Enum):
    NUEVO = "nuevo"
    USADO = "usado"
    DEFECTUOSO = "defectuoso"


class ReturnReason(str, enum.Enum):
    PEDIDO_EN_EXCESO = "pedido_en_exceso"
    DEFECTUOSO = "defectuoso"
    REPUESTO_INCORRECTO = "repuesto_incorrecto"
    OTRO = "otro"