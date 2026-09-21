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


class InvalidVehicleStatusTransitionError(BadRequestError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"No se puede pasar el vehículo de '{current}' a '{target}'.",
            error_code="invalid_vehicle_status_transition",
        )


class VehicleReservedByAnotherUserError(ConflictError):
    def __init__(self) -> None:
        super().__init__(
            "Este vehículo está reservado por otro vendedor.",
            error_code="vehicle_reserved_by_another_user",
        )


class BelowCostSaleRequiresAuthorizationError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "El precio de venta está por debajo del costo del vehículo. "
            "Esto requiere autorización de un rol superior (Administración).",
            error_code="below_cost_sale_requires_authorization",
        )


class BelowCostOverrideNoteRequiredError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Debes indicar el motivo para autorizar una venta por debajo del costo.",
            error_code="below_cost_override_note_required",
        )