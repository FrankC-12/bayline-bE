from decimal import Decimal

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError


class PartNotFoundError(NotFoundError):
    def __init__(self, part_id: str) -> None:
        super().__init__(f"Part '{part_id}' was not found.", error_code="part_not_found")


class PartCodeAlreadyExistsError(ConflictError):
    def __init__(self, code: str) -> None:
        super().__init__(
            f"A part with code '{code}' already exists.", error_code="part_code_already_exists"
        )


class PartCategoryNotFoundError(NotFoundError):
    def __init__(self, category_id: str) -> None:
        super().__init__(
            f"Part category '{category_id}' was not found.", error_code="part_category_not_found"
        )


class PartCategoryNameAlreadyExistsError(ConflictError):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"A part category named '{name}' already exists.",
            error_code="part_category_name_already_exists",
        )


class PartMeasureNotFoundError(NotFoundError):
    def __init__(self, measure_id: str) -> None:
        super().__init__(
            f"Part measure '{measure_id}' was not found.", error_code="part_measure_not_found"
        )


class PartMeasureNameAlreadyExistsError(ConflictError):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"A part measure named '{name}' already exists.",
            error_code="part_measure_name_already_exists",
        )


class VehicleModelBrandMismatchError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "El modelo seleccionado no pertenece a la marca seleccionada.",
            error_code="vehicle_model_brand_mismatch",
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


class NegativeMarginPriceError(BadRequestError):
    """Every ODS discount tier multiplies cost by >= 1.00, so a computed
    parts line price should never fall below its FIFO cost — this only
    fires on a rounding edge case (a cost with more precision than the
    quantized price, at the "Precio de costo" 0%-margin tier) or a future
    change that ever adds a tier below 100%. Blocks outright rather than
    offering an override — nothing legitimate should ever want to sell a
    parts line below what it cost."""

    def __init__(self, cost: Decimal, total: Decimal) -> None:
        # cost keeps its full precision in the message (not rounded to 2
        # decimals like total) — that's exactly what makes the shortfall
        # visible instead of both amounts printing the same rounded value.
        super().__init__(
            f"El precio de venta calculado (${total:.2f}) quedó por debajo del costo (${cost}). "
            "Revisa el margen configurado en la orden.",
            error_code="negative_margin_price",
        )