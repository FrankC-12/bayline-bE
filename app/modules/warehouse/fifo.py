"""Exact FIFO allocation shared by quotes, sales and warehouse consumption."""

from decimal import ROUND_HALF_UP, Decimal

from app.modules.warehouse.exceptions import InsufficientStockError

CENT = Decimal("0.01")
MICRO = Decimal("0.000001")


def allocate_fifo(lots, quantity):
    available = sum(lot.quantity_remaining for lot in lots)
    if available < quantity:
        raise InsufficientStockError(available, quantity)
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


def price_allocations(allocations, quantity, multiplier=Decimal("1.30")):
    cost = sum((Decimal(str(lot.unit_cost)) * take for lot, take in allocations), Decimal(0))
    total = (cost * multiplier).quantize(CENT, rounding=ROUND_HALF_UP)
    unit_price = (total / quantity).quantize(MICRO, rounding=ROUND_HALF_UP)
    unit_cost = (cost / quantity).quantize(MICRO, rounding=ROUND_HALF_UP)
    return unit_cost, unit_price, total
