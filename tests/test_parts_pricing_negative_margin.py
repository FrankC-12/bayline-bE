"""No ODS discount tier can legitimately price a parts line below its FIFO
cost (every multiplier is >= 1.00) — this is a defensive check, not a
reachable everyday scenario, guarding against a rounding edge case (a cost
with more precision than the quantized 2-decimal price) or a future tier
below 100%, so a below-cost line is blocked outright instead of silently
persisted."""

from decimal import Decimal

import pytest

from app.modules.parts.exceptions import NegativeMarginPriceError
from app.modules.parts.pricing import price_parts_cost


def test_normal_margin_tiers_never_trip_the_guard():
    for label in ("Costo + 30% (Sin Descuento)", "Costo + 20% (Descuento 10%)", "Costo + 10% (Descuento 20%)"):
        unit_price, total = price_parts_cost(Decimal("100.00"), 4, label)
        assert total >= Decimal("100.00")
        assert unit_price > 0


def test_precio_de_costo_with_an_exact_cost_does_not_trip_the_guard():
    unit_price, total = price_parts_cost(Decimal("31.25"), 1, "Precio de costo")
    assert total == Decimal("31.25")
    assert unit_price == Decimal("31.25")


def test_rounding_down_below_cost_at_precio_de_costo_is_blocked():
    """A FIFO-weighted average cost carries more precision than the 2-decimal
    price — at 0% margin, rounding it down (10.004 -> 10.00) would silently
    sell for a cent-fraction less than it cost."""
    with pytest.raises(NegativeMarginPriceError):
        price_parts_cost(Decimal("10.004"), 1, "Precio de costo")


def test_negative_margin_error_message_shows_both_the_rounded_price_and_the_full_precision_cost():
    with pytest.raises(NegativeMarginPriceError) as excinfo:
        price_parts_cost(Decimal("10.004"), 1, "Precio de costo")
    assert "$10.00" in excinfo.value.message  # the rounded price that would've been charged
    assert "10.004" in excinfo.value.message  # the actual, more-precise cost — visibly higher
    assert "por debajo del costo" in excinfo.value.message
