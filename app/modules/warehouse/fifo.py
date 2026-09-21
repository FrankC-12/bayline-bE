"""Exact FIFO allocation shared by quotes, sales and warehouse consumption."""

from decimal import ROUND_HALF_UP, Decimal

from app.modules.warehouse.exceptions import InsufficientStockError

CENT = Decimal("0.01")
MICRO = Decimal("0.000001")


def allocate_fifo(lots, quantity, *, part_id=None, part_name=None):
    available = sum(lot.quantity_remaining for lot in lots)
    if available < quantity:
        raise InsufficientStockError(available, quantity, part_id=part_id, part_name=part_name)
    remaining = quantity
    allocations = []
    for lot in lots:
        take = min(lot.quantity_remaining, remaining)
        if take > 0:
            allocations.append((lot, take))
            remaining -= take
        if remaining == 0:
            break
    return allocations


def allocate_fifo_preview(lots, quantity):
    """Like allocate_fifo, but never raises — allocates whatever stock is
    actually available and reports the rest as a shortfall instead of
    blocking. Used for the add-time preview on a service order's ODT (a
    client may bring their own part, or the shop may request it from
    another branch later): the real, blocking check happens at dispatch
    (mark_transfer_ordered / "pedir a almacén"), when stock actually leaves
    the shelf."""
    remaining = quantity
    allocations = []
    for lot in lots:
        if remaining <= 0:
            break
        take = min(lot.quantity_remaining, remaining)
        if take > 0:
            allocations.append((lot, take))
            remaining -= take
    return allocations, remaining


def price_allocations(allocations, quantity, multiplier=Decimal("1.30")):
    cost = sum((Decimal(str(lot.unit_cost)) * take for lot, take in allocations), Decimal(0))
    total = (cost * multiplier).quantize(CENT, rounding=ROUND_HALF_UP)
    unit_price = (total / quantity).quantize(MICRO, rounding=ROUND_HALF_UP)
    unit_cost = (cost / quantity).quantize(MICRO, rounding=ROUND_HALF_UP)
    return unit_cost, unit_price, total
