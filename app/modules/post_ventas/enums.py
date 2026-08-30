import enum


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