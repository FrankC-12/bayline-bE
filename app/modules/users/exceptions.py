from app.core.exceptions import BadRequestError, ConflictError, NotFoundError


class UserNotFoundError(NotFoundError):
    """Raised when a user does not exist."""

    def __init__(self, user_id: str) -> None:
        super().__init__(f"User '{user_id}' was not found.", error_code="user_not_found")


class EmailAlreadyExistsError(ConflictError):
    """Raised when the email is already taken by another user."""

    def __init__(self, email: str) -> None:
        super().__init__(
            f"A user with email '{email}' already exists.", error_code="email_already_exists"
        )


class HoldingRequiredForScopeError(BadRequestError):
    """Raised when a holding-scoped role is assigned without a holding_id."""

    def __init__(self) -> None:
        super().__init__(
            "A holding_id is required for roles with 'holding' scope.",
            error_code="holding_required_for_scope",
        )


class FilialRequiredForScopeError(BadRequestError):
    """Raised when a filial-scoped role is assigned without a filial_id."""

    def __init__(self) -> None:
        super().__init__(
            "A filial_id is required for roles with 'filial' scope.",
            error_code="filial_required_for_scope",
        )


class ScopeDoesNotAllowTenantError(BadRequestError):
    """Raised when a platform-scoped role is assigned a holding_id or filial_id."""

    def __init__(self) -> None:
        super().__init__(
            "Roles with 'platform' scope cannot be assigned a holding or filial.",
            error_code="scope_does_not_allow_tenant",
        )


class FilialHoldingMismatchError(BadRequestError):
    """Raised when the provided holding_id does not match the filial's actual holding."""

    def __init__(self) -> None:
        super().__init__(
            "The provided holding_id does not match the filial's holding.",
            error_code="filial_holding_mismatch",
        )


class PermissionOverridesNotAllowedError(BadRequestError):
    """Raised when permission overrides are set on a non filial-scoped user."""

    def __init__(self) -> None:
        super().__init__(
            "Permission overrides are only allowed for users with a filial-scoped role.",
            error_code="permission_overrides_not_allowed",
        )


class InvalidModuleIdError(BadRequestError):
    """Raised when a permission override references a module that does not exist."""

    def __init__(self, module_id: str) -> None:
        super().__init__(f"'{module_id}' is not a valid module id.", error_code="invalid_module_id")
