from app.core.exceptions import ConflictError, NotFoundError


class HoldingNotFoundError(NotFoundError):
    """Raised when a holding does not exist."""

    def __init__(self, holding_id: str) -> None:
        super().__init__(f"Holding '{holding_id}' was not found.", error_code="holding_not_found")


class HoldingSlugAlreadyExistsError(ConflictError):
    """Raised when trying to create or update a holding with a slug already in use."""

    def __init__(self, slug: str) -> None:
        super().__init__(
            f"A holding with slug '{slug}' already exists.",
            error_code="holding_slug_already_exists",
        )
