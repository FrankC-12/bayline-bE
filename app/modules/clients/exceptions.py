from app.core.exceptions import ConflictError, NotFoundError


class ClientNotFoundError(NotFoundError):
    """Raised when a client does not exist."""

    def __init__(self, client_id: str) -> None:
        super().__init__(f"El cliente '{client_id}' no fue encontrado.", error_code="client_not_found")


class VehicleNotFoundError(NotFoundError):
    """Raised when a vehicle does not exist."""

    def __init__(self, vehicle_id: str) -> None:
        super().__init__(f"El vehículo '{vehicle_id}' no fue encontrado.", error_code="vehicle_not_found")


class DocumentAlreadyExistsError(ConflictError):
    """Raised when a document number is already registered within the same filial."""

    def __init__(self, document: str) -> None:
        super().__init__(
            f"Ya existe un cliente registrado con la cédula/RIF '{document}' en esta filial.",
            error_code="document_already_exists",
        )
