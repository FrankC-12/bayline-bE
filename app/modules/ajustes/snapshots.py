"""Order creation freezes configuration; today's exchange rate remains live."""

from types import SimpleNamespace

DEFAULTS = dict(
    hourly_rate=25,
    commission_percentage=30,
    iva_percentage=16,
    igtf_percentage=3,
    iva_retention_default_percentage=0,
    islr_retention_default_percentage=0,
    workshop_warranty_days=90,
    workshop_warranty_km=5000,
    workshop_parts_warranty_days=90,
    workshop_parts_warranty_km=5000,
)


def settings_snapshot(settings):
    return {
        key: float(getattr(settings, key, value)) if settings else value
        for key, value in DEFAULTS.items()
    }


def order_settings(order, live_settings):
    values = settings_snapshot(live_settings)
    values.update(order.settings_snapshot or {})
    return SimpleNamespace(**values)
