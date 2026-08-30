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