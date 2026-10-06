"""Native PDF invoices. All text is escaped; document data comes from stored records."""

from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def render_invoice_pdf(data: dict) -> bytes:
    buffer = BytesIO()
    navy = colors.HexColor("#14243a")
    blue = colors.HexColor("#2458ac")
    pale = colors.HexColor("#edf3fb")
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle("InvoiceBody", fontName="Helvetica", fontSize=9, leading=13, textColor=navy)
    )
    styles.add(
        ParagraphStyle(
            "InvoiceTitle", fontName="Helvetica-Bold", fontSize=25, leading=30, textColor=navy
        )
    )
    styles.add(ParagraphStyle("InvoiceRight", parent=styles["InvoiceBody"], alignment=TA_RIGHT))

    def paragraph(value, style="InvoiceBody"):
        return Paragraph(escape(str(value or "")).replace("\n", "<br/>"), styles[style])

    def money(value):
        return f'{data.get("currency", "USD")} {float(value or 0):,.2f}'

    story = []
    heading = Table(
        [
            [
                paragraph(data.get("issuer", "Bayline"), "InvoiceTitle"),
                paragraph("FACTURA\n" + data["code"], "InvoiceRight"),
            ]
        ],
        colWidths=[112 * mm, 62 * mm],
    )
    heading.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 2, blue),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
            ]
        )
    )
    story.extend([heading, Spacer(1, 8 * mm)])
    if data.get("example"):
        story.extend([paragraph("EJEMPLO · Datos ilustrativos"), Spacer(1, 4 * mm)])
    details = Table(
        [
            [
                paragraph(
                    "CLIENTE\n"
                    + data.get("client", "")
                    + "\n"
                    + data.get("client_document", "")
                    + "\n"
                    + data.get("address", "")
                ),
                paragraph(
                    "EMISIÓN\n" + str(data.get("issued_at", "")) + "\n" + data.get("reference", "")
                ),
            ]
        ],
        colWidths=[112 * mm, 62 * mm],
    )
    details.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), pale),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.extend([details, Spacer(1, 6 * mm)])
    if data.get("vehicle"):
        story.extend([paragraph(data["vehicle"]), Spacer(1, 5 * mm)])
    rows = [
        [
            paragraph("Descripción"),
            paragraph("Cant."),
            paragraph("Precio " + data.get("currency", "USD")),
            paragraph("Importe " + data.get("currency", "USD")),
        ]
    ]
    for line in data.get("lines", []):
        rows.append(
            [
                paragraph(line["description"]),
                paragraph(line.get("quantity", "—")),
                paragraph(
                    (
                        f'{float(line["unit_price"]):,.2f}'
                        if line.get("unit_price") is not None
                        else "—"
                    ),
                    "InvoiceRight",
                ),
                paragraph(
                    f'{float(line["total"]):,.2f}' if line.get("total") is not None else "—",
                    "InvoiceRight",
                ),
            ]
        )
    table = Table(rows, colWidths=[98 * mm, 15 * mm, 29 * mm, 32 * mm], repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), pale),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, 0), 1, blue),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#dce3ed")),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.extend([table, Spacer(1, 5 * mm)])
    totals = [
        [paragraph(label), paragraph(money(value), "InvoiceRight")]
        for label, value in data["totals"]
    ]
    totals_table = Table(totals, colWidths=[105 * mm, 69 * mm])
    totals_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, -1), (-1, -1), pale),
                ("LINEABOVE", (0, -1), (-1, -1), 1, blue),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend([totals_table, Spacer(1, 7 * mm), paragraph("COBROS Y SALDO", "Heading3")])
    for payment in data.get("payments", []):
        story.append(paragraph(payment))
    story.extend(
        [paragraph("Saldo pendiente: " + money(data.get("pending", 0))), Spacer(1, 5 * mm)]
    )
    for note in data.get("notes", []):
        story.append(paragraph(note))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#dce3ed"))
        canvas.line(18 * mm, 19 * mm, 192 * mm, 19 * mm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(navy)
        canvas.drawString(18 * mm, 14 * mm, "Bayline · " + str(data["code"]))
        canvas.drawRightString(192 * mm, 14 * mm, f"Página {doc.page}")
        canvas.restoreState()

    SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=25 * mm,
        title="Factura " + data["code"],
        author=data.get("issuer", "Bayline"),
    ).build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def from_service_invoice(document: dict) -> dict:
    summary = document["summary"]
    lines = []
    for transfer in summary["transfers"]:
        for line in transfer["lines"]:
            part = document["parts"].get(str(line["part_id"]), {})
            lines.append(
                {
                    "description": f'{part.get("code", "")} · {part.get("name", "Repuesto")}',
                    "quantity": line["quantity"],
                    "unit_price": line["unit_price"],
                    "total": line["subtotal"],
                }
            )
    # The saved summary carries the authoritative labor subtotal, including its pricing rules.
    for task in summary["tasks"]:
        lines.append(
            {
                "description": (
                    f'{task["code_snapshot"]} · {task["name_snapshot"]} '
                    f'({task["hours_snapshot"]} h)'
                ),
                "total": None,
            }
        )
    if summary["tasks"]:
        lines.append({"description": "Total mano de obra", "total": summary["labor_subtotal"]})
    rate = document.get("bcv_rate")
    paid = sum(
        float(p["amount"]) / (float(rate) if p["currency"].lower() == "bs" and rate else 1)
        for p in document.get("payments", [])
    )
    retained = float(document.get("iva_retention_amount", 0)) + float(
        document.get("islr_retention_amount", 0)
    )
    totals = [
        ("Subtotal", float(summary["parts_subtotal"]) + float(summary["labor_subtotal"])),
        (f'IVA ({summary["iva_percentage"]}%)', summary["iva_amount"]),
        (f'IGTF ({document["igtf_percentage"]}%)', document["igtf_amount"]),
        ("Total", document["total_usd"]),
    ]
    if retained:
        totals.extend(
            [
                ("Retenciones", -retained),
                ("Total a cobrar", float(document["total_usd"]) - retained),
            ]
        )
    warranty_notes = []
    for warranty in document.get("warranties", []):
        durations = []
        if warranty.get("duration_days") is not None:
            durations.append(f'{warranty["duration_days"]} días')
        if warranty.get("duration_km") is not None:
            durations.append(f'{warranty["duration_km"]:,} km')
        term = " / ".join(durations) if durations else "Sin vencimiento"
        coverage = "Mano de obra" if warranty["coverage"] == "labor" else "Repuestos"
        warranty_notes.append(
            f'Garantía {warranty["task"]} · {coverage}: {warranty["name"]} · {term}'
            + (" (lo que ocurra primero)" if len(durations) == 2 else "")
        )
    return {
        "issuer": document["filial_name"],
        "code": document["code"],
        "issued_at": document["issued_at"],
        "reference": "Orden " + document["order_code"],
        "client": document["client_name"],
        "client_document": document["client_document"],
        "address": document["client_address"],
        "vehicle": document["vehicle"],
        "lines": lines,
        "totals": totals,
        "payments": [
            f'{p["account_name"]} · {p["currency"].upper()} {float(p["amount"]):,.2f}'
            for p in document.get("payments", [])
        ],
        "pending": max(0, float(document["total_usd"]) - retained - paid),
        "notes": warranty_notes
        + [
            "Importes y saldo al emitir la factura.",
            (
                f"Tasa aplicada: Bs. {float(rate):,.8f} por USD"
                if rate
                else "Moneda de referencia: USD"
            ),
            "Referencia de pago: " + str(document.get("payment_reference") or "—"),
        ],
    }
