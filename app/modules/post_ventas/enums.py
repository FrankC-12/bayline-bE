import enum


class VehicleWarrantySource(str, enum.Enum):
    VENTA = "venta"
    MANUAL = "manual"


class WorkshopWarrantyCoverage(str, enum.Enum):
    MANO_DE_OBRA = "mano_de_obra"
    REPUESTO = "repuesto"


class WarrantyPolicyAppliesTo(str, enum.Enum):
    MANO_DE_OBRA = "mano_de_obra"
    REPUESTOS = "repuestos"
    AMBAS = "ambas"


class WarrantyPolicyCoveredBy(str, enum.Enum):
    LA_CASA = "la_casa"
    FABRICA_IMPORTADOR = "fabrica_importador"
    PROVEEDOR = "proveedor"


class WarrantyPolicyScope(str, enum.Enum):
    SOLO_PIEZA = "solo_pieza"
    PIEZA_MAS_INSTALACION = "pieza_mas_instalacion"


class WarrantyPolicyStatus(str, enum.Enum):
    ACTIVA = "activa"
    INACTIVA = "inactiva"


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