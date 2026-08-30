from app.core.exceptions import BadRequestError, ConflictError, NotFoundError


class RoleNotFoundError(NotFoundError):
    """Raised when a role does not exist."""

    def __init__(self, role_id: str) -> None:
        super().__init__(f"Role '{role_id}' was not found.", error_code="role_not_found")


class RoleSlugAlreadyExistsError(ConflictError):
    """Raised when a role slug is already in use."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"A role with slug '{slug}' already exists.", error_code="role_slug_already_exists")


class InvalidModuleIdError(BadRequestError):
    """Raised when a permission references a module that does not exist in the catalog."""

    def __init__(self, module_id: str) -> None:
        super().__init__(f"'{module_id}' is not a valid module id.", error_code="invalid_module_id")


class PermissionsNotAllowedForScopeError(BadRequestError):
    """Raised when module permissions are set on a Platform or Holding scoped role."""

    def __init__(self, scope: str) -> None:
        super().__init__(
            f"Roles with scope '{scope}' cannot declare module permissions.",
            error_code="permissions_not_allowed_for_scope",
        )
