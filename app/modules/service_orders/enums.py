import enum


class ServiceOrderStatus(str, enum.Enum):
    PENDIENTE = "pendiente"
    EN_PROGRESO = "en_progreso"
    COMPLETADO = "completado"
    ORDEN_CERRADA = "orden_cerrada"
    CANCELADO = "cancelado"


# "Tipo de ODS" used to be this fixed enum — it's now a filial-scoped,
# admin-manageable catalog (ServiceOrderTypeCatalog, in models.py). These
# codes identify the 6 rows seeded for every filial (see
# ServiceOrderService.seed_default_order_types); only those system rows ever
# carry a code/claim_type/is_selectable — anything an admin creates from the
# Postventas screen behaves like SYSTEM_ORDER_TYPE_REGULAR.
SYSTEM_ORDER_TYPE_REGULAR = "regular"
# No Garantías/MPT module or screen exists yet — not selectable manually.
# Kept in the catalog for when that module ships.
SYSTEM_ORDER_TYPE_MPT = "mpt"
# Opened automatically when a WarrantyClaim is converted to an order — see
# ServiceOrderService.convert_warranty_claim_to_order. Never picked by hand.
SYSTEM_ORDER_TYPE_RETRABAJO = "retrabajo"
# These three ARE picked by hand, at manual ODS creation — each requires
# linking an existing, authorized WarrantyClaim of the matching type for
# the same vehicle (ServiceOrder.warranty_claim_id), enforced via the
# catalog row's claim_type. A separate path from RETRABAJO's automatic
# conversion: this lets an advisor open the ODS directly from intake and
# reference the claim, instead of going through "Convertir a ODS" from the
# Reclamos screen.
SYSTEM_ORDER_TYPE_GARANTIA_FABRICA = "garantia_fabrica"
SYSTEM_ORDER_TYPE_COMEBACK = "comeback"
SYSTEM_ORDER_TYPE_CAMPANA = "campana"


class TaskStatus(str, enum.Enum):
    PENDIENTE = "pendiente"
    EN_ESPERA_DE_REPUESTOS = "en_espera_de_repuestos"
    EN_PROGRESO = "en_progreso"
    COMPLETADA = "completada"
    CANCELADA = "cancelada"


class TransferStatus(str, enum.Enum):
    PENDIENTE = "pendiente"
    PEDIDO = "pedido"
    # Parts physically handed over to the técnico — confirmed from the
    # almacén side (see AlmacenService.list_service_order_requests), not
    # automatic. Lets the elapsed-time counter shown there pause instead of
    # running forever once the request is actually fulfilled.
    COMPLETADO = "completado"


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


class UpsellSeverity(str, enum.Enum):
    URGENTE = "urgente"
    PRONTO = "pronto"
    MONITOREAR = "monitorear"


class UpsellDiscardReason(str, enum.Enum):
    YA_REPARADO_OTRO_TALLER = "ya_reparado_otro_taller"
    CLIENTE_NO_LO_QUIERE = "cliente_no_lo_quiere"
    YA_NO_APLICA = "ya_no_aplica"
    OTRO = "otro"


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