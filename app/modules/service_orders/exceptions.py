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


class InvalidTransferStatusTransitionError(BadRequestError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"No se puede mover un ODT de '{current}' a '{target}'.",
            error_code="invalid_transfer_status_transition",
        )


class TransferLineNotEditableError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Ya no se puede modificar esta línea — el ODT fue marcado como Pedido.",
            error_code="transfer_line_not_editable",
        )


class TaskAndTechnicianRequiredError(BadRequestError):
    def __init__(self, missing_task: bool, missing_technician: bool) -> None:
        parts = []
        if missing_task:
            parts.append("una tarea agregada")
        if missing_technician:
            parts.append("un técnico asignado")
        joined = " y ".join(parts)
        super().__init__(
            f"No se pueden pedir repuestos sin tener {joined} en la orden.",
            error_code="task_and_technician_required",
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


class ServiceOrderNotCancelledError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Solo se puede reabrir una orden cancelada.",
            error_code="service_order_not_cancelled",
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
            "Solo se puede registrar un reclamo de garantía tipo comeback sobre una orden ya facturada.",
            error_code="order_not_invoiced",
        )


class WarrantyClaimReferenceMismatchError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "El servicio o el repuesto elegido no pertenece a esta orden.",
            error_code="warranty_claim_reference_mismatch",
        )


class WarrantyClaimRequiredForOrderTypeError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Selecciona el reclamo asociado a esta orden.",
            error_code="warranty_claim_required_for_order_type",
        )


class WarrantyClaimOrderMismatchError(BadRequestError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason, error_code="warranty_claim_order_mismatch")


class WarrantyClaimNotFoundError(NotFoundError):
    def __init__(self, claim_id: str) -> None:
        super().__init__(f"Warranty claim '{claim_id}' was not found.", error_code="warranty_claim_not_found")


class FailureCategoryRequiredError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "No se puede autorizar este reclamo sin haber registrado la causa de la falla.",
            error_code="failure_category_required",
        )


class WarrantyClaimAlreadyDecidedError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Este reclamo ya fue autorizado o rechazado.",
            error_code="warranty_claim_already_decided",
        )


class WarrantyClaimNotAuthorizedError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Este reclamo debe estar autorizado antes de convertirlo en una orden de servicio.",
            error_code="warranty_claim_not_authorized",
        )


class WarrantyClaimAlreadyConvertedError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Este reclamo ya fue convertido en una orden de servicio.",
            error_code="warranty_claim_already_converted",
        )


class ServiceOrderRequiredForComebackError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Un reclamo de tipo comeback debe referenciar la orden de servicio que lo originó.",
            error_code="service_order_required_for_comeback",
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
