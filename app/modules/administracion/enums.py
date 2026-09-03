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
    OTRO = "otro"
