import enum


class InspectionStatus(str, enum.Enum):
    EN_PROCESO = "en_proceso"
    COMPLETADA = "completada"