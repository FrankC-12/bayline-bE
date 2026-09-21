import enum


class ServiceOrderStatus(str, enum.Enum):
    PENDIENTE = "pendiente"
    EN_PROGRESO = "en_progreso"
    COMPLETADO = "completado"
    ORDEN_CERRADA = "orden_cerrada"
    CANCELADO = "cancelado"


class ServiceOrderType(str, enum.Enum):
    REGULAR = "regular"
    # No Garantías/MPT module or screen exists yet — the frontend's "Nueva
    # ODS" form deliberately hides this option so it can't be picked with
    # no workflow behind it. Kept in the API/enum for when that module ships.
    MPT = "mpt"
    # Opened automatically when a WarrantyClaim is converted to an order —
    # see ServiceOrderService.convert_warranty_claim_to_order. Never picked
    # by hand.
    RETRABAJO = "retrabajo"


class TaskStatus(str, enum.Enum):
    PENDIENTE = "pendiente"
    COMPLETADA = "completada"


class TransferStatus(str, enum.Enum):
    PENDIENTE = "pendiente"
    PEDIDO = "pedido"


class UpsellStatus(str, enum.Enum):
    PENDIENTE = "pendiente"
    APROBADO = "aprobado"
    POSPUESTO = "pospuesto"
    RECHAZADO = "rechazado"


class UpsellApprovalChannel(str, enum.Enum):
    """How the client actually agreed to pay for the additional work —
    distinct from Client.contact_preference (a durable profile setting),
    this is a point-in-time record of this one approval."""

    WHATSAPP = "whatsapp"
    LLAMADA = "llamada"
    CORREO = "correo"
    SMS = "sms"
    PRESENCIAL = "presencial"


class ReworkFailureCategory(str, enum.Enum):
    MANO_DE_OBRA = "mano_de_obra"
    REPUESTO_DEFECTUOSO = "repuesto_defectuoso"
    ERROR_DIAGNOSTICO = "error_diagnostico"
    MAL_USO_CLIENTE = "mal_uso_cliente"
    NO_DETERMINADA = "no_determinada"


class WarrantyClaimType(str, enum.Enum):
    FABRICA = "fabrica"
    COMEBACK = "comeback"
    REPUESTO_PROVEEDOR = "repuesto_proveedor"
    CAMPANA_RECALL = "campana_recall"


class WarrantyClaimStatus(str, enum.Enum):
    SOLICITADO = "solicitado"
    AUTORIZADO = "autorizado"
    RECHAZADO = "rechazado"
    CONVERTIDO_A_ODS = "convertido_a_ods"


class ServiceOrderPayer(str, enum.Enum):
    CLIENTE = "cliente"
    GARANTIA_TALLER = "garantia_taller"
    GARANTIA_FABRICA = "garantia_fabrica"
    PLAN_MANTENIMIENTO = "plan_mantenimiento"
    PROVEEDOR = "proveedor"