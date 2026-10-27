"""Printable sale document; mileage is frozen at the time of sale."""

from datetime import UTC
from html import escape
from zoneinfo import ZoneInfo


def render_sale_document(sale, vehicle):
    def value(text):
        return escape(str(text)) if text is not None else "No registrado"

    snapshot = getattr(sale, "vehicle_snapshot", None) or {}
    currency = "Bs." if snapshot.get("price_currency", vehicle.price_currency) == "VES" else "$"
    timestamp = sale.created_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    sale_date = timestamp.astimezone(ZoneInfo("America/Caracas")).strftime("%d/%m/%Y")
    price = f"{float(sale.final_price):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    rows = [
        ("Cliente", sale.client_name),
        ("Documento", sale.client_document),
        ("Modalidad", sale.sale_type.value),
        ("Precio final", f"{currency} {price}"),
    ]
    rows.extend(
        (line.split(": ", 1)[0], line.split(": ", 1)[1])
        for line in sale_vehicle_description(sale, vehicle).splitlines()
    )
    rows.extend(
        [
            ("Pago USD", getattr(sale, "usd_payment_channel", None)),
            ("Pago Bs", getattr(sale, "bs_payment_channel", None)),
        ]
    )
    body = "".join(
        f"<tr><th>{escape(label)}</th><td>{value(text)}</td></tr>" for label, text in rows
    )
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Documento de venta {value(sale.code)}</title>
<style>body{{font:16px Arial,sans-serif;color:#17263a;max-width:800px;margin:40px auto;padding:24px}}
h1{{font-size:26px}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #ddd}}th{{width:35%}}@media print{{body{{margin:0;padding:12px}}}}</style>
</head><body><h1>Documento de venta · {value(sale.code)}</h1>
<p>Fecha: {value(sale_date)}</p><table>{body}</table>
<p>El kilometraje corresponde al registrado al momento de la venta.</p></body></html>"""


VEHICLE_DESCRIPTION_FIELDS = (
    ("brand", "Marca"),
    ("model", "Modelo"),
    ("version", "Versión"),
    ("year", "Año"),
    ("condition", "Condición"),
    ("vin", "VIN"),
    ("plate", "Placa"),
    ("sku", "SKU"),
    ("color", "Color"),
    ("engine_type", "Motor"),
    ("fuel_type", "Combustible"),
    ("transmission", "Transmisión"),
    ("seats", "Asientos"),
    ("upholstery", "Tapicería"),
    ("previous_owners", "Dueños anteriores"),
)


def sale_vehicle_description(sale, vehicle):
    snapshot = getattr(sale, "vehicle_snapshot", None)
    values = (
        snapshot
        if snapshot is not None
        else {name: getattr(vehicle, name, None) for name, _ in VEHICLE_DESCRIPTION_FIELDS}
    )
    rows = []
    for name, label in VEHICLE_DESCRIPTION_FIELDS:
        if name == "previous_owners" and values.get("condition") not in ("usado",):
            continue
        item = values.get(name)
        if hasattr(item, "value"):
            item = item.value
        rows.append(f"{label}: {item if item is not None and item != '' else 'No registrado'}")
    mileage = sale.mileage_at_sale
    rows.append(
        "Kilometraje al vender: "
        + (f"{mileage:,} km".replace(",", ".") if mileage is not None else "No registrado")
    )
    return "\n".join(rows)
