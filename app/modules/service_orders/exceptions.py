from app.core.exceptions import BadRequestError, ConflictError, NotFoundError


class ServiceOrderNotFoundError(NotFoundError):
    def __init__(self, order_id: str) -> None:
        super().__init__(
            f"Service order '{order_id}' was not found.", error_code="service_order_not_found"
        )


class BayNotFoundError(NotFoundError):
    def __init__(self, bay_id: str) -> None:
        super().__init__(f"Bay '{bay_id}' was not found.", error_code="bay_not_found")


class InvalidStatusTransitionError(BadRequestError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Cannot move a service order from '{current}' to '{target}'.",
            error_code="invalid_status_transition",
        )


class TaskNotFoundError(NotFoundError):
    def __init__(self, task_id: str) -> None:
        super().__init__(f"Task '{task_id}' was not found.", error_code="task_not_found")


class TransferNotFoundError(NotFoundError):
    def __init__(self, transfer_id: str) -> None:
        super().__init__(
            f"Transfer '{transfer_id}' was not found.", error_code="transfer_not_found"
        )


class UpsellNotFoundError(NotFoundError):
    def __init__(self, upsell_id: str) -> None:
        super().__init__(f"Upsell '{upsell_id}' was not found.", error_code="upsell_not_found")


class ServiceOrderReadOnlyError(ConflictError):
    def __init__(self) -> None:
        super().__init__(
            "Las órdenes facturadas, cerradas o canceladas son de solo lectura.",
            error_code="service_order_read_only",
        )


class InvoiceNotFoundError(NotFoundError):
    def __init__(self, invoice_id: str) -> None:
        super().__init__(f"Invoice '{invoice_id}' was not found.", error_code="invoice_not_found")


class InvoiceAlreadyCollectedError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Esta factura ya fue cobrada en su totalidad.",
            error_code="invoice_already_collected",
        )


class ReceivableCurrencyMismatchError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "La cuenta elegida para cobrar debe manejar USD.",
            error_code="receivable_currency_mismatch",
        )


class OrderNotInvoicedError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Solo se puede registrar un reclamo de garantía sobre una orden ya facturada.",
            error_code="order_not_invoiced",
        )


class ReworkClaimReferenceMismatchError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "El servicio o el repuesto elegido no pertenece a esta orden.",
            error_code="rework_claim_reference_mismatch",
        )


class ReworkClaimNotFoundError(NotFoundError):
    def __init__(self, claim_id: str) -> None:
        super().__init__(f"Rework claim '{claim_id}' was not found.", error_code="rework_claim_not_found")


class ReworkClaimAlreadyClosedError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Este reclamo de retrabajo ya está cerrado.",
            error_code="rework_claim_already_closed",
        )


class FailureCategoryRequiredError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "No se puede cerrar un reclamo de retrabajo sin haber registrado la causa de la falla.",
            error_code="failure_category_required",
        )


class ReworkClaimAlreadyAuthorizedError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Este reclamo de retrabajo ya fue autorizado o rechazado.",
            error_code="rework_claim_already_authorized",
        )


class VehicleWarrantyRequiredError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "El vehículo no tiene una garantía de fábrica vigente. Si de todas formas se va a autorizar, "
            "marca la excepción explícita y registra por qué.",
            error_code="vehicle_warranty_required",
        )


class WarrantyOverrideNoteRequiredError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Debes registrar por qué se autoriza sin garantía vigente.",
            error_code="warranty_override_note_required",
        )


class WarrantyClaimWarrantyMismatchError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Una de las garantías seleccionadas no corresponde a este vehículo.",
            error_code="warranty_claim_warranty_mismatch",
        )
