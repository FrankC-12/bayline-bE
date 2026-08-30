import enum


class VehicleCondition(str, enum.Enum):
    NUEVO = "nuevo"
    USADO = "usado"


class VehicleStatus(str, enum.Enum):
    EN_TRANSITO = "en_transito"
    DISPONIBLE = "disponible"
    EN_PREPARACION = "en_preparacion"
    RESERVADO = "reservado"
    VENDIDO = "vendido"


class FuelType(str, enum.Enum):
    GASOLINA = "gasolina"
    DIESEL = "diesel"
    HIBRIDO = "hibrido"
    ELECTRICO = "electrico"


class TransmissionType(str, enum.Enum):
    AUTOMATICA = "automatica"
    MANUAL = "manual"


class SaleType(str, enum.Enum):
    CONTADO = "contado"
    FINANCIADO = "financiado"