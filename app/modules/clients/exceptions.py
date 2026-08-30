from app.core.exceptions import ConflictError, NotFoundError


class ClientNotFoundError(NotFoundError):
    """Raised when a client does not exist."""

    def __init__(self, client_id: str) -> None:
        super().__init__(f"Client '{client_id}' was not found.", error_code="client_not_found")


class DocumentAlreadyExistsError(ConflictError):
    """Raised when a document number is already registered within the same filial."""

    def __init__(self, document: str) -> None:
        super().__init__(
            f"A client with document '{document}' already exists in this filial.",
            error_code="document_already_exists",
        )