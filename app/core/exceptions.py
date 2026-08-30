class DomainError(Exception):
    """Base class for all domain-level errors in the application."""

    status_code: int = 500
    error_code: str = "internal_error"

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if error_code:
            self.error_code = error_code


class BadRequestError(DomainError):
    """Raised when the client sent a malformed or invalid request."""

    status_code = 400
    error_code = "bad_request"


class UnauthorizedError(DomainError):
    """Raised when authentication is missing or invalid."""

    status_code = 401
    error_code = "unauthorized"


class ForbiddenError(DomainError):
    """Raised when the authenticated user lacks permission for the action."""

    status_code = 403
    error_code = "forbidden"


class NotFoundError(DomainError):
    """Raised when a requested resource does not exist."""

    status_code = 404
    error_code = "not_found"


class ConflictError(DomainError):
    """Raised when a request conflicts with the current state of a resource."""

    status_code = 409
    error_code = "conflict"


class UnprocessableEntityError(DomainError):
    """Raised when the request is well-formed but semantically invalid."""

    status_code = 422
    error_code = "unprocessable_entity"
