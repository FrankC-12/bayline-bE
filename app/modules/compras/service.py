import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.compras.enums import VehiclePurchaseOrderStatus
from app.modules.compras.exceptions import (
    DuplicateVehicleVinError,
    InvalidVehiclePurchaseOrderStatusTransitionError,
    NoUnitsToInvoiceError,
    UnitAlreadyInvoicedError,
    VehiclePurchaseOrderLineNotFoundError,
    VehiclePurchaseOrderLineOverReceivedError,
    VehiclePurchaseOrderNotFoundError,
)
from app.modules.compras.models import (
    VehiclePurchaseOrder,
    VehiclePurchaseOrderInvoice,
    VehiclePurchaseOrderLine,
    VehiclePurchaseOrderReception,
)
from app.modules.compras.schemas import (
    ReceivedUnitRead,
    ReceptionCreate,
    ReceptionRead,
    VehiclePurchaseOrderCreate,
    VehiclePurchaseOrderDetailRead,
    VehiclePurchaseOrderInvoiceCreate,
    VehiclePurchaseOrderInvoiceRead,
    VehiclePurchaseOrderLineRead,
    VehiclePurchaseOrderRead,
)
from app.modules.concesionario.enums import VehicleCondition, VehicleStatus
from app.modules.concesionario.models import DealershipVehicle

# A CANCELADA order is dead; RECIBIDA/CONCILIADA are only reachable once
# every line's ordered quantity has actually arrived (see _recompute_status).
CANCELABLE_STATUSES = {VehiclePurchaseOrderStatus.ENVIADA, VehiclePurchaseOrderStatus.PARCIALMENTE_RECIBIDA}


class ComprasService:
    """Vehicle purchase orders (OC) — a purchase order to an importer/brand
    for new stock, received across one or more deliveries, each unit
    individually identified by VIN at reception. Distinct from
    AdministracionService's PurchaseRequest (parts restocking, fungible
    quantities, single-shot reception)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _next_sequence(self, filial_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.max(VehiclePurchaseOrder.sequence_number)).where(
                VehiclePurchaseOrder.filial_id == filial_id
            )
        )
        current_max = result.scalar()
        return (current_max or 3000) + 1

    async def _get_order_model(self, order_id: uuid.UUID) -> VehiclePurchaseOrder:
        result = await self.db.execute(
            select(VehiclePurchaseOrder)
            .options(
                selectinload(VehiclePurchaseOrder.lines),
                selectinload(VehiclePurchaseOrder.receptions),
                selectinload(VehiclePurchaseOrder.invoices),
            )
            .where(VehiclePurchaseOrder.id == order_id)
            # An order already in the identity map (e.g. just fetched at the
            # top of add_reception/add_invoice, before a new reception/invoice
            # existed) must not serve a stale, incomplete receptions/invoices
            # collection once one is added later in the same call.
            .execution_options(populate_existing=True)
        )
        order = result.scalar_one_or_none()
        if order is None:
            raise VehiclePurchaseOrderNotFoundError(str(order_id))
        return order

    async def _units_by_line(self, line_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[DealershipVehicle]]:
        if not line_ids:
            return {}
        result = await self.db.execute(
            select(DealershipVehicle).where(DealershipVehicle.purchase_order_line_id.in_(line_ids))
        )
        units_by_line: dict[uuid.UUID, list[DealershipVehicle]] = {}
        for unit in result.scalars().all():
            units_by_line.setdefault(unit.purchase_order_line_id, []).append(unit)
        return units_by_line

    async def _to_read(self, order: VehiclePurchaseOrder) -> VehiclePurchaseOrderRead:
        units_by_line = await self._units_by_line([line.id for line in order.lines])
        return VehiclePurchaseOrderRead(
            id=order.id,
            filial_id=order.filial_id,
            code=order.code,
            supplier_id=order.supplier_id,
            status=order.status,
            created_at=order.created_at,
            lines=[
                VehiclePurchaseOrderLineRead(
                    id=line.id,
                    brand=line.brand,
                    model=line.model,
                    version=line.version,
                    year=line.year,
                    color=line.color,
                    quantity=line.quantity,
                    quantity_received=len(units_by_line.get(line.id, [])),
                )
                for line in order.lines
            ],
        )

    async def _to_detail_read(self, order: VehiclePurchaseOrder) -> VehiclePurchaseOrderDetailRead:
        base = await self._to_read(order)
        line_ids = [line.id for line in order.lines]
        units_by_line = await self._units_by_line(line_ids)
        all_units = [unit for units in units_by_line.values() for unit in units]
        units_by_reception: dict[uuid.UUID, list[DealershipVehicle]] = {}
        for unit in all_units:
            if unit.reception_id is not None:
                units_by_reception.setdefault(unit.reception_id, []).append(unit)

        def _unit_read(unit: DealershipVehicle) -> ReceivedUnitRead:
            return ReceivedUnitRead(
                id=unit.id,
                purchase_order_line_id=unit.purchase_order_line_id,
                vin=unit.vin,
                brand=unit.brand,
                model=unit.model,
                year=unit.year,
                color=unit.color,
                cost_price=float(unit.cost_price) if unit.cost_price is not None else None,
                cost_is_estimated=unit.cost_is_estimated,
                purchase_order_invoice_id=unit.purchase_order_invoice_id,
            )

        receptions = [
            ReceptionRead(
                id=reception.id,
                received_at=reception.received_at,
                notes=reception.notes,
                units=[_unit_read(u) for u in units_by_reception.get(reception.id, [])],
            )
            for reception in order.receptions
        ]
        invoices = [
            VehiclePurchaseOrderInvoiceRead(
                id=invoice.id,
                invoice_number=invoice.invoice_number,
                total_amount=float(invoice.total_amount),
                currency=invoice.currency,
                issued_at=invoice.issued_at,
                unit_count=sum(1 for u in all_units if u.purchase_order_invoice_id == invoice.id),
                created_at=invoice.created_at,
            )
            for invoice in order.invoices
        ]
        return VehiclePurchaseOrderDetailRead(**base.model_dump(), receptions=receptions, invoices=invoices)

    async def list_vehicle_purchase_orders(self, filial_id: uuid.UUID) -> list[VehiclePurchaseOrderRead]:
        result = await self.db.execute(
            select(VehiclePurchaseOrder)
            .options(selectinload(VehiclePurchaseOrder.lines))
            .where(VehiclePurchaseOrder.filial_id == filial_id)
            .order_by(VehiclePurchaseOrder.sequence_number.desc())
        )
        return [await self._to_read(order) for order in result.scalars().all()]

    async def get_vehicle_purchase_order(self, order_id: uuid.UUID) -> VehiclePurchaseOrderDetailRead:
        return await self._to_detail_read(await self._get_order_model(order_id))

    async def get_order_filial(self, order_id: uuid.UUID) -> uuid.UUID:
        order = await self._get_order_model(order_id)
        return order.filial_id

    async def create_vehicle_purchase_order(
        self, payload: VehiclePurchaseOrderCreate, created_by_user_id: uuid.UUID | None
    ) -> VehiclePurchaseOrderRead:
        sequence_number = await self._next_sequence(payload.filial_id)
        order = VehiclePurchaseOrder(
            filial_id=payload.filial_id,
            sequence_number=sequence_number,
            supplier_id=payload.supplier_id,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(order)
        await self.db.flush()
        for line in payload.lines:
            self.db.add(
                VehiclePurchaseOrderLine(
                    purchase_order_id=order.id,
                    brand=line.brand,
                    model=line.model,
                    version=line.version,
                    year=line.year,
                    color=line.color,
                    quantity=line.quantity,
                )
            )
        await self.db.commit()
        return await self._to_read(await self._get_order_model(order.id))

    def _recompute_status(self, order: VehiclePurchaseOrder, units_by_line: dict[uuid.UUID, list[DealershipVehicle]]) -> None:
        if order.status in (VehiclePurchaseOrderStatus.CANCELADA, VehiclePurchaseOrderStatus.CONCILIADA):
            return
        received_counts = [len(units_by_line.get(line.id, [])) for line in order.lines]
        fully_received = all(count >= line.quantity for count, line in zip(received_counts, order.lines))
        any_received = any(count > 0 for count in received_counts)
        if fully_received:
            all_units = [unit for units in units_by_line.values() for unit in units]
            if all_units and all(unit.purchase_order_invoice_id is not None for unit in all_units):
                order.status = VehiclePurchaseOrderStatus.CONCILIADA
            else:
                order.status = VehiclePurchaseOrderStatus.RECIBIDA
        elif any_received:
            order.status = VehiclePurchaseOrderStatus.PARCIALMENTE_RECIBIDA

    async def add_reception(
        self, order_id: uuid.UUID, payload: ReceptionCreate, received_by_user_id: uuid.UUID | None
    ) -> VehiclePurchaseOrderDetailRead:
        order = await self._get_order_model(order_id)
        lines_by_id = {line.id: line for line in order.lines}
        units_by_line = await self._units_by_line(list(lines_by_id.keys()))

        vins_in_batch: set[str] = set()
        for unit_input in payload.units:
            line = lines_by_id.get(unit_input.purchase_order_line_id)
            if line is None:
                raise VehiclePurchaseOrderLineNotFoundError()
            vin = unit_input.vin.strip().upper()
            if vin in vins_in_batch:
                raise DuplicateVehicleVinError(vin)
            vins_in_batch.add(vin)
            existing = await self.db.execute(select(DealershipVehicle).where(DealershipVehicle.vin == vin))
            if existing.scalar_one_or_none() is not None:
                raise DuplicateVehicleVinError(vin)

        # Validate quantities per line (a batch can span several lines).
        requested_by_line: dict[uuid.UUID, int] = {}
        for unit_input in payload.units:
            requested_by_line[unit_input.purchase_order_line_id] = (
                requested_by_line.get(unit_input.purchase_order_line_id, 0) + 1
            )
        for line_id, requested in requested_by_line.items():
            line = lines_by_id[line_id]
            already_received = len(units_by_line.get(line_id, []))
            if already_received + requested > line.quantity:
                raise VehiclePurchaseOrderLineOverReceivedError(
                    line.brand, line.model, line.quantity, already_received, requested
                )

        reception = VehiclePurchaseOrderReception(
            purchase_order_id=order.id, received_by_user_id=received_by_user_id, notes=payload.notes
        )
        self.db.add(reception)
        await self.db.flush()

        for unit_input in payload.units:
            line = lines_by_id[unit_input.purchase_order_line_id]
            vin = unit_input.vin.strip().upper()
            unit = DealershipVehicle(
                filial_id=order.filial_id,
                status=VehicleStatus.EN_PREPARACION,
                condition=VehicleCondition.NUEVO,
                brand=line.brand,
                model=line.model,
                version=line.version,
                year=line.year,
                color=line.color,
                vin=vin,
                sku=vin,
                price_cash=0,
                price_financed=0,
                cost_price=None,
                cost_is_estimated=True,
                purchase_order_line_id=line.id,
                reception_id=reception.id,
            )
            self.db.add(unit)
            units_by_line.setdefault(line.id, []).append(unit)

        self._recompute_status(order, units_by_line)
        await self.db.commit()
        return await self._to_detail_read(await self._get_order_model(order.id))

    async def add_invoice(
        self, order_id: uuid.UUID, payload: VehiclePurchaseOrderInvoiceCreate, recorded_by_user_id: uuid.UUID | None
    ) -> VehiclePurchaseOrderDetailRead:
        order = await self._get_order_model(order_id)
        line_ids = [line.id for line in order.lines]
        units_by_line = await self._units_by_line(line_ids)
        all_units = [unit for units in units_by_line.values() for unit in units]

        if payload.dealership_vehicle_ids is not None:
            wanted = set(payload.dealership_vehicle_ids)
            target_units = [unit for unit in all_units if unit.id in wanted]
            if len(target_units) != len(wanted):
                raise VehiclePurchaseOrderLineNotFoundError()
            if any(unit.purchase_order_invoice_id is not None for unit in target_units):
                raise UnitAlreadyInvoicedError()
        else:
            target_units = [unit for unit in all_units if unit.purchase_order_invoice_id is None]

        if not target_units:
            raise NoUnitsToInvoiceError()

        invoice = VehiclePurchaseOrderInvoice(
            filial_id=order.filial_id,
            purchase_order_id=order.id,
            invoice_number=payload.invoice_number,
            total_amount=Decimal(str(payload.total_amount)),
            currency=payload.currency,
            issued_at=payload.issued_at,
            recorded_by_user_id=recorded_by_user_id,
        )
        self.db.add(invoice)
        await self.db.flush()

        # Split the invoice total evenly across the units it covers — the
        # remainder (from rounding to cents) lands on the last unit so the
        # sum of per-unit costs matches total_amount exactly.
        total = Decimal(str(payload.total_amount))
        count = len(target_units)
        base_unit_cost = (total / count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        allocated = base_unit_cost * (count - 1)
        remainder = (total - allocated).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        for index, unit in enumerate(target_units):
            unit.cost_price = float(remainder if index == count - 1 else base_unit_cost)
            unit.cost_is_estimated = False
            unit.purchase_order_invoice_id = invoice.id

        self._recompute_status(order, units_by_line)
        await self.db.commit()
        return await self._to_detail_read(await self._get_order_model(order.id))

    async def cancel_vehicle_purchase_order(self, order_id: uuid.UUID) -> VehiclePurchaseOrderRead:
        order = await self._get_order_model(order_id)
        if order.status not in CANCELABLE_STATUSES:
            raise InvalidVehiclePurchaseOrderStatusTransitionError(
                order.status.value, VehiclePurchaseOrderStatus.CANCELADA.value
            )
        order.status = VehiclePurchaseOrderStatus.CANCELADA
        await self.db.commit()
        return await self._to_read(await self._get_order_model(order.id))
