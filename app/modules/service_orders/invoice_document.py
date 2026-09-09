"""A standalone, printable invoice generated exclusively from its saved snapshot."""

from html import escape


def render_invoice(document: dict) -> str:
    def text(value):
        return escape(str(value))

    def amount(value):
        return f"{float(value or 0):,.2f}"

    summary = document["summary"]
    rows = []
    for transfer in summary["transfers"]:
        for line in transfer["lines"]:
            part = document["parts"].get(line["part_id"], {})
            rows.append(
                f"<tr><td>{text(part.get('code', ''))} · {text(part.get('name', 'Repuesto'))}</td>"
                f"<td>{line['quantity']}</td><td>{float(line['unit_price']):.6f}</td>"
                f"<td>{amount(line['subtotal'])}</td></tr>"
            )
    tasks = "".join(
        f"<li>{text(t['code_snapshot'])} · {text(t['name_snapshot'])} "
        f"({text(t['hours_snapshot'])} h)</li>"
        for t in summary["tasks"]
    )
    payments = "".join(
        f"<tr><td>{text(p['currency'].upper())}</td><td>{text(p['account_name'])}</td>"
        f"<td>{amount(p['amount'])}</td></tr>"
        for p in document["payments"]
    )
    rate = (
        f"Bs. {document['bcv_rate']:.8f} / USD · Fecha valor {text(document['bcv_date'])}"
        if document["bcv_rate"] is not None
        else "No utilizada (pago íntegro en USD)"
    )
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{text(document['code'])}</title><style>
body{{font:14px Arial,sans-serif;color:#14243a;max-width:900px;margin:40px auto;padding:24px}}
h1{{font-size:28px}}h2{{font-size:18px;margin-top:30px}}header{{border-bottom:3px solid #2458ac}}
table{{border-collapse:collapse;width:100%;margin:15px 0}}th,td{{padding:10px;border-bottom:1px solid #ddd;text-align:right}}
th:first-child,td:first-child{{text-align:left}}.totals{{margin-left:auto;max-width:390px}}
p{{line-height:1.6}}footer{{margin-top:30px;font-size:11px;color:#657080}}
@media print{{body{{margin:0;max-width:none;padding:12mm}}tr{{break-inside:avoid}}}}
</style></head><body><header><h1>Factura {text(document['code'])}</h1>
<p>{text(document['filial_name'])}<br>Orden {text(document['order_code'])} · {text(document['issued_at'])}</p></header>
<h2>Cliente</h2><p>{text(document['client_name'])} · {text(document['client_document'])}<br>
{text(document['client_address'])}<br>{text(document['vehicle'])}</p>
<h2>Repuestos</h2><table><thead><tr><th>Descripción</th><th>Cantidad</th><th>Precio USD</th><th>Importe USD</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="4">Sin repuestos</td></tr>'}</tbody></table>
<p>Nivel de precio: {text(summary['discount_label'])}</p><h2>Mano de obra</h2><ul>{tasks or '<li>Sin tareas</li>'}</ul>
<table class="totals"><tr><td>Repuestos</td><td>$ {amount(summary['parts_subtotal'])}</td></tr>
<tr><td>Mano de obra</td><td>$ {amount(summary['labor_subtotal'])}</td></tr>
<tr><td>IVA ({text(summary['iva_percentage'])}%)</td><td>$ {amount(summary['iva_amount'])}</td></tr>
<tr><td>IGTF ({text(document['igtf_percentage'])}% sobre USD {amount(document['usd_base'])})</td><td>$ {amount(document['igtf_amount'])}</td></tr>
<tr><th>Total equivalente USD</th><th>$ {amount(document['total_usd'])}</th></tr></table>
<h2>Comprobante de pago</h2><p>Método: {text({'usd':'USD','bs':'Bs.','mixed':'Mixto'}[document['payment_method']])}<br>
Tasa BCV: {rate}<br>Referencia: {text(document['payment_reference']) or '—'}</p>
<table><tr><th>Moneda</th><th>Cuenta receptora</th><th>Importe recibido</th></tr>{payments}</table>
<p>Recibido: USD {amount(document['due_usd'])} + Bs. {amount(document['due_bs'])}. Saldo pendiente: 0.00.</p>
<footer>Documento {text(document['id'])} · Importes registrados al emitir la factura.</footer></body></html>"""
