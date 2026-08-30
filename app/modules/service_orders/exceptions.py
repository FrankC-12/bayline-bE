from app.core.exceptions import BadRequestError, NotFoundError


class ServiceOrderNotFoundError(NotFoundError):
    def __init__(self, order_id: str) -> None:
        super().__init__(
            f"Service order '{order_id}' was not found.", error_code="service_order_not_found"
        )


class BayNotFoundError(NotFoundError):
    def __init__(self, bay_id: str) -> None:
        super().__init__(f"Bay '{bay_id}' was not found.", error_code="bay_not_found")


class InvalidStatusTransitionError(BadRequestError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Cannot move a service order from '{current}' to '{target}'.",
            error_code="invalid_status_transition",
        )


class TaskNotFoundError(NotFoundError):
    def __init__(self, task_id: str) -> None:
        super().__init__(f"Task '{task_id}' was not found.", error_code="task_not_found")


class TransferNotFoundError(NotFoundError):
    def __init__(self, transfer_id: str) -> None:
        super().__init__(f"Transfer '{transfer_id}' was not found.", error_code="transfer_not_found")


class UpsellNotFoundError(NotFoundError):
    def __init__(self, upsell_id: str) -> None:
        super().__init__(f"Upsell '{upsell_id}' was not found.", error_code="upsell_not_found")