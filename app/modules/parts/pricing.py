"""Supported parts margins, shared by counter sales and service orders."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from app.modules.parts.exceptions import NegativeMarginPriceError

DiscountLabel = Literal[
    "Costo + 30% (Sin Descuento)",
    "Costo + 20% (Descuento 10%)",
    "Costo + 10% (Descuento 20%)",
    "Precio de costo",
]
DEFAULT_DISCOUNT: DiscountLabel = "Costo + 30% (Sin Descuento)"
PARTS_MULTIPLIERS = {
    "Costo + 30% (Sin Descuento)": Decimal("1.30"),
    "Costo + 20% (Descuento 10%)": Decimal("1.20"),
    "Costo + 10% (Descuento 20%)": Decimal("1.10"),
    "Precio de costo": Decimal("1.00"),
}


def price_parts_cost(cost: Decimal, quantity: int, discount_label: DiscountLabel):
    total = (cost * PARTS_MULTIPLIERS[discount_label]).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    # Every tier multiplies by >= 1.00, so this should never trip in
    # practice — it's a safety net against a rounding edge case (a cost
    # with more precision than the quantized price) or a future tier below
    # 100%, not a reachable everyday scenario.
    if total < cost:
        raise NegativeMarginPriceError(cost, total)
    unit_price = (total / quantity).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return unit_price, total
