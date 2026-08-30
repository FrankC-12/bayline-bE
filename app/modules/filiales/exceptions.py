from app.core.exceptions import ConflictError, NotFoundError


class FilialNotFoundError(NotFoundError):
    """Raised when a filial does not exist."""

    def __init__(self, filial_id: str) -> None:
        super().__init__(f"Filial '{filial_id}' was not found.", error_code="filial_not_found")


class FilialSlugAlreadyExistsError(ConflictError):
    """Raised when a slug is already used by another filial within the same holding."""

    def __init__(self, slug: str) -> None:
        super().__init__(
            f"A filial with slug '{slug}' already exists in this holding.",
            error_code="filial_slug_already_exists",
        )
