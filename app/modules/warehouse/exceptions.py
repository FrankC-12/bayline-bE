from app.core.exceptions import BadRequestError, NotFoundError


class WarehouseNotFoundError(NotFoundError):
    def __init__(self, warehouse_id: str) -> None:
        super().__init__(f"Warehouse '{warehouse_id}' was not found.", error_code="warehouse_not_found")


class TransferNotFoundError(NotFoundError):
    def __init__(self, transfer_id: str) -> None:
        super().__init__(f"Transfer '{transfer_id}' was not found.", error_code="transfer_not_found")


class SameWarehouseError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Origin and destination warehouse must be different.", error_code="same_warehouse"
        )


class InsufficientStockError(BadRequestError):
    def __init__(self, available: int, requested: int) -> None:
        super().__init__(
            f"Only {available} units available at the origin warehouse, requested {requested}.",
            error_code="insufficient_stock",
        )


class InvalidTransferStatusTransitionError(BadRequestError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Cannot move a transfer from '{current}' to '{target}'.",
            error_code="invalid_transfer_status_transition",
        )