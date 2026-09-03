from app.core.exceptions import ForbiddenError, UnauthorizedError


class InvalidCredentialsError(UnauthorizedError):
    """Raised when the email/password combination does not match any active user."""

    def __init__(self) -> None:
        super().__init__("Invalid email or password.", error_code="invalid_credentials")


class InactiveUserError(UnauthorizedError):
    """Raised when a user with valid credentials is not in 'activo' status."""

    def __init__(self) -> None:
        super().__init__("This user account is not active.", error_code="inactive_user")


class InvalidTokenError(UnauthorizedError):
    """Raised when the Bearer token is missing, malformed, or expired."""

    def __init__(self) -> None:
        super().__init__(
            "The provided token is invalid or has expired.", error_code="invalid_token"
        )


class InvalidRefreshTokenError(UnauthorizedError):
    """Raised when a refresh token is missing, malformed, or expired."""

    def __init__(self) -> None:
        super().__init__(
            "The refresh token is invalid or has expired.", error_code="invalid_refresh_token"
        )


class InsufficientPermissionsError(ForbiddenError):
    """Raised when an authenticated user's scope does not allow the requested action."""

    def __init__(self) -> None:
        super().__init__(
            "You do not have permission to perform this action.",
            error_code="insufficient_permissions",
        )
