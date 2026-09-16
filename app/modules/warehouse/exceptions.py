from app.core.exceptions import BadRequestError, ConflictError, NotFoundError


class WarehouseNotFoundError(NotFoundError):
    def __init__(self, warehouse_id: str) -> None:
        super().__init__(f"Warehouse '{warehouse_id}' was not found.", error_code="warehouse_not_found")


class PartLotNotFoundError(NotFoundError):
    def __init__(self, lot_id: str) -> None:
        super().__init__(f"Part lot '{lot_id}' was not found.", error_code="part_lot_not_found")


class StockInReasonNotFoundError(NotFoundError):
    def __init__(self, reason_id: str) -> None:
        super().__init__(
            f"Stock-in reason '{reason_id}' was not found.", error_code="stock_in_reason_not_found"
        )


class StockInReasonNameAlreadyExistsError(ConflictError):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"A stock-in reason named '{name}' already exists.",
            error_code="stock_in_reason_name_already_exists",
        )


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
            f"Solo hay {available} unidades disponibles en el almacén de origen, se pidieron {requested}.",
            error_code="insufficient_stock",
        )


class InvalidTransferStatusTransitionError(BadRequestError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Cannot move a transfer from '{current}' to '{target}'.",
            error_code="invalid_transfer_status_transition",
        )