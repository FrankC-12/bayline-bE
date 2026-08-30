from app.core.exceptions import BadRequestError, NotFoundError


class InspectionNotFoundError(NotFoundError):
    def __init__(self, inspection_id: str) -> None:
        super().__init__(
            f"Inspection '{inspection_id}' was not found.", error_code="inspection_not_found"
        )


class InspectionAlreadyLinkedError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "This inspection is already linked to a service order.",
            error_code="inspection_already_linked",
        )


class CannotDeleteLinkedInspectionError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Cannot delete an inspection that is linked to a service order.",
            error_code="cannot_delete_linked_inspection",
        )