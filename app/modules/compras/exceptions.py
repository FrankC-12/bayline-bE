from app.core.exceptions import BadRequestError, NotFoundError


class VehiclePurchaseOrderNotFoundError(NotFoundError):
    def __init__(self, order_id: str) -> None:
        super().__init__(
            f"La orden de compra '{order_id}' no fue encontrada.", error_code="vehicle_purchase_order_not_found"
        )


class VehiclePurchaseOrderLineNotFoundError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Una de las líneas indicadas no pertenece a esta orden de compra.",
            error_code="vehicle_purchase_order_line_not_found",
        )


class VehiclePurchaseOrderLineOverReceivedError(BadRequestError):
    def __init__(self, brand: str, model: str, ordered: int, already_received: int, requested: int) -> None:
        super().__init__(
            f"'{brand} {model}': se pidieron {ordered}, ya se recibieron {already_received} y esta "
            f"recepción intenta agregar {requested} más — excede la cantidad de la línea.",
            error_code="vehicle_purchase_order_line_over_received",
        )


class DuplicateVehicleVinError(BadRequestError):
    def __init__(self, vin: str) -> None:
        super().__init__(
            f"Ya existe un vehículo registrado con el VIN '{vin}'.", error_code="duplicate_vehicle_vin"
        )


class InvalidVehiclePurchaseOrderStatusTransitionError(BadRequestError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"No se puede mover una orden de compra de '{current}' a '{target}'.",
            error_code="invalid_vehicle_purchase_order_status_transition",
        )


class NoUnitsToInvoiceError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "No hay unidades recibidas pendientes de facturar en esta orden de compra.",
            error_code="no_units_to_invoice",
        )


class UnitAlreadyInvoicedError(BadRequestError):
    def __init__(self) -> None:
        super().__init__(
            "Una de las unidades seleccionadas ya tiene una factura registrada.",
            error_code="unit_already_invoiced",
        )
