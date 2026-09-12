from app.core.exceptions import ConflictError, NotFoundError


class TemparioNotFoundError(NotFoundError):
    def __init__(self, tempario_id: str) -> None:
        super().__init__(f"Tempario '{tempario_id}' was not found.", error_code="tempario_not_found")


class TemparioCodeAlreadyExistsError(ConflictError):
    def __init__(self, code: str) -> None:
        super().__init__(
            f"A tempario with code '{code}' already exists.", error_code="tempario_code_already_exists"
        )


class MaintenancePlanNotFoundError(NotFoundError):
    def __init__(self, plan_id: str) -> None:
        super().__init__(
            f"Maintenance plan '{plan_id}' was not found.", error_code="maintenance_plan_not_found"
        )


class VehicleWarrantyAlreadyExistsError(ConflictError):
    def __init__(self, vin: str) -> None:
        super().__init__(
            f"Ya existe una garantía de fábrica registrada para el VIN '{vin}'.",
            error_code="vehicle_warranty_already_exists",
        )


class VehicleWarrantyNotFoundError(NotFoundError):
    def __init__(self, identifier: str) -> None:
        super().__init__(
            f"Vehicle warranty '{identifier}' was not found.", error_code="vehicle_warranty_not_found"
        )