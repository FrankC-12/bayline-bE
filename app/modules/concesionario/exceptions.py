from app.core.exceptions import BadRequestError, ConflictError, NotFoundError


class VehicleNotFoundError(NotFoundError):
    def __init__(self, vehicle_id: str) -> None:
        super().__init__(f"Vehicle '{vehicle_id}' was not found.", error_code="vehicle_not_found")


class VinAlreadyExistsError(ConflictError):
    def __init__(self, vin: str) -> None:
        super().__init__(f"A vehicle with VIN '{vin}' already exists.", error_code="vin_already_exists")


class SaleDetailsRequiredError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Sale details (client, advisor, type, price) are required to mark a vehicle as sold.",
            error_code="sale_details_required",
        )