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