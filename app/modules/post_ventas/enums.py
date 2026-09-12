import enum


class VehicleWarrantySource(str, enum.Enum):
    VENTA = "venta"
    MANUAL = "manual"


class WorkshopWarrantyCoverage(str, enum.Enum):
    MANO_DE_OBRA = "mano_de_obra"
    REPUESTO = "repuesto"


class TemparioCategory(str, enum.Enum):
    MANTENIMIENTO_PREVENTIVO = "mantenimiento_preventivo"
    FRENOS = "frenos"
    SUSPENSION = "suspension"
    TRANSMISION = "transmision"
    NEUMATICOS = "neumaticos"
    MOTOR = "motor"
    ELECTRICO = "electrico"
    AIRE_ACONDICIONADO = "aire_acondicionado"
    OTRO = "otro"


CATEGORY_PREFIXES: dict[TemparioCategory, str] = {
    TemparioCategory.MANTENIMIENTO_PREVENTIVO: "MP",
    TemparioCategory.FRENOS: "FR",
    TemparioCategory.SUSPENSION: "SU",
    TemparioCategory.TRANSMISION: "TR",
    TemparioCategory.NEUMATICOS: "NE",
    TemparioCategory.MOTOR: "MT",
    TemparioCategory.ELECTRICO: "EL",
    TemparioCategory.AIRE_ACONDICIONADO: "AC",
    TemparioCategory.OTRO: "OT",
}