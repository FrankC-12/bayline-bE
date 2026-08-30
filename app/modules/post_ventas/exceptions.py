from app.core.exceptions import ConflictError, NotFoundError


class TemparioNotFoundError(NotFoundError):
    def __init__(self, tempario_id: str) -> None:
        super().__init__(f"Tempario '{tempario_id}' was not found.", error_code="tempario_not_found")


class TemparioCodeAlreadyExistsError(ConflictError):
    def __init__(self, code: str) -> None:
        super().__init__(
            f"A tempario with code '{code}' already exists.", error_code="tempario_code_already_exists"
        )