import uuid

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError


class WarehouseNotFoundError(NotFoundError):
    def __init__(self, warehouse_id: str) -> None:
        super().__init__(f"Warehouse '{warehouse_id}' was not found.", error_code="warehouse_not_found")


class PartLotNotFoundError(NotFoundError):
    def __init__(self, lot_id: str) -> None:
        super().__init__(f"Part lot '{lot_id}' was not found.", error_code="part_lot_not_found")


class NoStockAtWarehouseError(NotFoundError):
    def __init__(self) -> None:
        super().__init__(
            "Este repuesto no tiene existencia en este almacén.",
            error_code="no_stock_at_warehouse",
        )


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
    def __init__(
        self,
        available: int,
        requested: int,
        *,
        part_id: uuid.UUID | None = None,
        part_name: str | None = None,
    ) -> None:
        label = f" de {part_name}" if part_name else ""
        message = f"Solo hay {available} unidades disponibles{label} en el almacén de origen, se pidieron {requested}."
        # Anchors the message to the specific line/part that ran short — a
        # sale or transfer can have several lines, and without this the
        # caller has no way to tell which one it was about.
        details = [{"field": str(part_id), "message": message}] if part_id is not None else None
        super().__init__(message, error_code="insufficient_stock", details=details)


class InvalidTransferStatusTransitionError(BadRequestError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Cannot move a transfer from '{current}' to '{target}'.",
            error_code="invalid_transfer_status_transition",
        )