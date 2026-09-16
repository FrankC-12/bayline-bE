from app.core.exceptions import ConflictError, NotFoundError


class VehicleBrandNotFoundError(NotFoundError):
    """Raised when a vehicle brand does not exist (or belongs to another holding)."""

    def __init__(self, brand_id: str) -> None:
        super().__init__(f"Vehicle brand '{brand_id}' was not found.", error_code="vehicle_brand_not_found")


class VehicleBrandNameAlreadyExistsError(ConflictError):
    """Raised when a brand name is already used within the same holding."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"A vehicle brand named '{name}' already exists.", error_code="vehicle_brand_name_already_exists"
        )


class VehicleModelNotFoundError(NotFoundError):
    """Raised when a vehicle model does not exist (or belongs to another holding)."""

    def __init__(self, model_id: str) -> None:
        super().__init__(f"Vehicle model '{model_id}' was not found.", error_code="vehicle_model_not_found")


class VehicleModelNameAlreadyExistsError(ConflictError):
    """Raised when a model name is already used within the same brand."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"A vehicle model named '{name}' already exists for this brand.",
            error_code="vehicle_model_name_already_exists",
        )
