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