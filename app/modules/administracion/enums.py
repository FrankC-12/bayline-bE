import enum


class SupplierType(str, enum.Enum):
    FABRICANTE = "fabricante"
    NACIONAL = "nacional"
    IMPORTADOR = "importador"


class SupplierStatus(str, enum.Enum):
    ACTIVO = "activo"
    INACTIVO = "inactivo"


class PurchaseRequestStatus(str, enum.Enum):
    ENVIADA = "enviada"
    COTIZADA = "cotizada"
    PAGADA = "pagada"
    RECIBIDA = "recibida"
    CONCILIADA = "conciliada"
    CANCELADA = "cancelada"


class ClaimStatus(str, enum.Enum):
    PENDIENTE_ENVIO = "pendiente_envio"
    ENVIADO = "enviado"
    APROBADO = "aprobado"
    RECHAZADO = "rechazado"
    RESUELTO = "resuelto"


class ClaimResolution(str, enum.Enum):
    CARGO_CLIENTE = "cargo_cliente"
    COSTO_TALLER = "costo_taller"


class WarrantySubmissionStatus(str, enum.Enum):
    BORRADOR = "borrador"
    PRESENTADA = "presentada"
    PAGADA = "pagada"


class AccountCurrency(str, enum.Enum):
    USD = "usd"
    BS = "bs"
    EUR = "eur"


class SupplierPaymentMethod(str, enum.Enum):
    TRANSFERENCIA = "transferencia"
    PAGO_MOVIL = "pago_movil"
    ZELLE = "zelle"
    EFECTIVO = "efectivo"
    OTRO = "otro"


class AccountType(str, enum.Enum):
    CORRIENTE = "corriente"
    AHORRO = "ahorro"
    CAJA = "caja"


class IncomeSource(str, enum.Enum):
    AUTOMATICO = "automatico"
    MANUAL = "manual"


class ExpenseCategory(str, enum.Enum):
    NOMINA_COMISIONES = "nomina_comisiones"
    SERVICIOS = "servicios"
    COMPRAS_PROVEEDORES = "compras_proveedores"
    ALQUILER = "alquiler"
    MANTENIMIENTO = "mantenimiento"
    MARKETING = "marketing"
    IMPUESTOS_TASAS = "impuestos_tasas"
    GARANTIA_RECHAZADA = "garantia_rechazada"
    OTRO = "otro"


class IncomeConcept(str, enum.Enum):
    """Closed concept list for a manual income entry — Egresos already had
    one (ExpenseCategory); Ingresos didn't, so any amount could be typed in
    under free-text description with no classification at all."""

    COBRO_CLIENTE = "cobro_cliente"
    REEMBOLSO_HOLDING = "reembolso_holding"
    APORTE_SOCIO = "aporte_socio"
    VENTA_ACTIVO = "venta_activo"
    GARANTIA_MARCA = "garantia_marca"
    FI_INTERMEDIACION = "fi_intermediacion"
    OTRO_INGRESO = "otro_ingreso"


class CounterpartyType(str, enum.Enum):
    """Who's on the other side of a manual movement. Cliente/Proveedor
    reference the existing catalogs; Tercero/Socio have no catalog to
    reference so they're captured as free text (counterparty_name)."""

    CLIENTE = "cliente"
    PROVEEDOR = "proveedor"
    TERCERO = "tercero"
    SOCIO = "socio"


class MovementSourceType(str, enum.Enum):
    """What generated an Income/Expense entry when it wasn't typed in by
    hand — lets the account-detail screen link a movement straight back to
    the document that produced it instead of just showing its description."""

    VEHICLE_SALE = "vehicle_sale"
    PART_SALE = "part_sale"
    SERVICE_ORDER = "service_order"
    SUPPLIER_CLAIM = "supplier_claim"
    WARRANTY_SUBMISSION = "warranty_submission"
