from app.core.exceptions import BadRequestError, NotFoundError


class SupplierNotFoundError(NotFoundError):
    def __init__(self, supplier_id: str) -> None:
        super().__init__(f"Supplier '{supplier_id}' was not found.", error_code="supplier_not_found")


class PurchaseRequestNotFoundError(NotFoundError):
    def __init__(self, request_id: str) -> None:
        super().__init__(
            f"Purchase request '{request_id}' was not found.", error_code="purchase_request_not_found"
        )


class ClaimNotFoundError(NotFoundError):
    def __init__(self, claim_id: str) -> None:
        super().__init__(f"Claim '{claim_id}' was not found.", error_code="claim_not_found")


class AccountNotFoundError(NotFoundError):
    def __init__(self, account_id: str) -> None:
        super().__init__(f"Account '{account_id}' was not found.", error_code="account_not_found")


class InvalidPurchaseStatusTransitionError(BadRequestError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Cannot move a purchase request from '{current}' to '{target}'.",
            error_code="invalid_purchase_status_transition",
        )


class QuoteRequiredError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Unit costs for every line are required to mark a request as 'Cotizada'.",
            error_code="quote_required",
        )


class WarehouseRequiredError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "A destination warehouse is required to mark a request as 'Recibida'.",
            error_code="warehouse_required",
        )


class ClaimNotRejectedError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Solo se puede resolver una reclamación que el proveedor rechazó.",
            error_code="claim_not_rejected",
        )


class ClaimMustBeResolvedThroughResolveEndpointError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Una reclamación rechazada solo puede marcarse como resuelta a través del flujo de resolución.",
            error_code="claim_must_use_resolve_endpoint",
        )


class ClaimAmountRequiredError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "La reclamación necesita un monto para poder resolverse.",
            error_code="claim_amount_required",
        )


class ClaimClientRequiredError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Debes asociar un cliente a la reclamación para cargarle el cobro.",
            error_code="claim_client_required",
        )


class ClaimAccountRequiredError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Debes elegir la cuenta que absorbe el costo del taller.",
            error_code="claim_account_required",
        )


class ClaimCurrencyMismatchError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "La cuenta elegida no maneja la misma moneda que la reclamación.",
            error_code="claim_currency_mismatch",
        )


class WarrantySubmissionNotFoundError(NotFoundError):
    def __init__(self, submission_id: str) -> None:
        super().__init__(
            f"Warranty submission '{submission_id}' was not found.", error_code="warranty_submission_not_found"
        )


class WarrantySubmissionAlreadyExistsError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Ya existe una presentación para ese período y moneda en esta filial.",
            error_code="warranty_submission_already_exists",
        )


class WarrantySubmissionNotEditableError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Esta presentación ya no se puede modificar en su estado actual.",
            error_code="warranty_submission_not_editable",
        )


class WarrantySubmissionEmptyError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "No se puede presentar una lista sin facturas de garantía.",
            error_code="warranty_submission_empty",
        )


class InvalidWarrantySubmissionTransitionError(BadRequestError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Cannot move a warranty submission from '{current}' to '{target}'.",
            error_code="invalid_warranty_submission_transition",
        )


class FutureEntryDateError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "No se puede registrar un movimiento con fecha futura.",
            error_code="future_entry_date",
        )


class ClosedPeriodEntryDateError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "La fecha del movimiento cae en un período ya cerrado — solo se puede registrar dentro del mes en curso.",
            error_code="closed_period_entry_date",
        )


class ExchangeRateRequiredError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "No hay tasa BCV registrada para la fecha del movimiento — no se puede convertir a USD.",
            error_code="exchange_rate_required",
        )


class AttachmentRequiredError(BadRequestError):
    def __init__(self, threshold_usd: float) -> None:
        super().__init__(
            f"Los movimientos de ${threshold_usd:,.2f} o más requieren un soporte adjunto.",
            error_code="attachment_required",
        )


class CounterpartyMismatchError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "La contraparte no corresponde con el tipo seleccionado.",
            error_code="counterparty_mismatch",
        )


class EntryNotFoundError(NotFoundError):
    def __init__(self, entry_id: str) -> None:
        super().__init__(f"Movement '{entry_id}' was not found.", error_code="entry_not_found")


class EntryAlreadyReversedError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Este movimiento ya fue reversado.",
            error_code="entry_already_reversed",
        )