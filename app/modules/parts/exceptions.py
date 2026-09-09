from app.core.exceptions import BadRequestError, ConflictError, NotFoundError


class PartNotFoundError(NotFoundError):
    def __init__(self, part_id: str) -> None:
        super().__init__(f"Part '{part_id}' was not found.", error_code="part_not_found")


class PartCodeAlreadyExistsError(ConflictError):
    def __init__(self, code: str) -> None:
        super().__init__(
            f"A part with code '{code}' already exists.", error_code="part_code_already_exists"
        )


class PartSaleNotFoundError(NotFoundError):
    def __init__(self, sale_id: str) -> None:
        super().__init__(f"Part sale '{sale_id}' was not found.", error_code="part_sale_not_found")


class InvalidSaleStatusTransitionError(BadRequestError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Cannot move a part sale from '{current}' to '{target}'.",
            error_code="invalid_sale_status_transition",
        )


class InvalidReturnDestinationError(BadRequestError):
    def __init__(self, condition: str) -> None:
        super().__init__(
            f"A part returned in '{condition}' condition can only be sent to a write-off "
            "(Baja/merma) destination, not back into sellable inventory.",
            error_code="invalid_return_destination",
        )


class MissingReturnPhotoError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "A part return requires at least one evidence photo.",
            error_code="missing_return_photo",
        )


class DispatchQuantityRequiredError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Confirm the dispatched quantity for every line before marking the sale as 'pedido'.",
            error_code="dispatch_quantity_required",
        )


class DispatchQuantityMismatchError(BadRequestError):
    def __init__(self, mismatched_part_ids: list[str]) -> None:
        super().__init__(
            "Dispatched quantity doesn't match what was sold for part(s): "
            f"{', '.join(mismatched_part_ids)}.",
            error_code="dispatch_quantity_mismatch",
        )