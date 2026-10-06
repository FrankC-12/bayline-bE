"""Printable sale document; mileage is frozen at the time of sale."""

from datetime import UTC
from html import escape
from zoneinfo import ZoneInfo


def render_sale_document(sale, vehicle):
    def value(text):
        return escape(str(text)) if text is not None else "No registrado"

    mileage = (
        f"{sale.mileage_at_sale:,} km".replace(",", ".")
        if sale.mileage_at_sale is not None
        else "No registrado"
    )
    currency = "Bs." if vehicle.price_currency == "VES" else "$"
    timestamp = sale.created_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    sale_date = timestamp.astimezone(ZoneInfo("America/Caracas")).strftime("%d/%m/%Y")
    price = f"{float(sale.final_price):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    rows = [
        ("Cliente", sale.client_name),
        ("Documento", sale.client_document),
        ("Vehículo", f"{vehicle.brand} {vehicle.model}"),
        ("Año", vehicle.year),
        ("VIN", vehicle.vin),
        ("Placa", vehicle.plate),
        ("Kilometraje al vender", mileage),
        ("Modalidad", sale.sale_type.value),
        ("Precio final", f"{currency} {price}"),
    ]
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
