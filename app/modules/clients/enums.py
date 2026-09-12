import enum


class ClientType(str, enum.Enum):
    PARTICULAR = "particular"
    EMPRESA = "empresa"


class DocumentType(str, enum.Enum):
    V = "V"
    J = "J"
    E = "E"
    G = "G"


class ContactPreference(str, enum.Enum):
    WHATSAPP = "whatsapp"
    LLAMADA = "llamada"
    CORREO = "correo"
    SMS = "sms"


class AddressType(str, enum.Enum):
    HOGAR = "hogar"
    TRABAJO = "trabajo"
    OTRO = "otro"


class FuelType(str, enum.Enum):
    GASOLINA = "gasolina"
    DIESEL = "diesel"
    HIBRIDO = "hibrido"
    ELECTRICO = "electrico"


class TransmissionType(str, enum.Enum):
    MANUAL = "manual"
    AUTOMATICA = "automatica"


class MaintenancePlanEntryStatus(str, enum.Enum):
    """A plan entry's status for one specific vehicle — always computed live
    from the vehicle's current mileage/age and its Service Order history,
    never stored, so it can't drift out of sync."""

    PENDIENTE = "pendiente"
    VENCIDO = "vencido"
    CUMPLIDO = "cumplido"
    OMITIDO = "omitido"