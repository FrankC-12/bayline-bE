"""Holding contabilidad report."""

from collections import defaultdict
from decimal import Decimal

from app.modules.holdings.control_common import D, ReportContext, table


async def render(self, ctx: ReportContext):
    from app.modules.administracion.service import AdministracionService

    svc = AdministracionService(self.db)
    p = (
        await svc.get_profitability(ctx.filial_id, ctx.start, ctx.end)
        if ctx.filial_id
        else await svc.get_profitability_for_holding(ctx.holding_id, ctx.start, ctx.end)
    )
    ctx.report.notes.append(
        "Los márgenes son utilidad / ventas × 100. Una pérdida puede superar el 100 % de las ventas; no se limita artificialmente el indicador."
    )
    ctx.metric("Ventas netas operativas", p.net_sales_total, "money")
    ctx.metric("Utilidad bruta operativa", p.gross_profit_total, "money")
    ctx.metric("Resultado operativo", p.net_profit, "money")
    ctx.metric("Margen operativo", p.net_margin * 100, "percent")
    ctx.report.tables.append(
        table(
            "Estado de resultados operativo",
            [
                ("label", "Departamento"),
                ("net_sales", "Ventas netas", "money"),
                ("direct_cost", "Costo directo", "money"),
                ("gross_profit", "Utilidad bruta", "money"),
                ("margin", "Margen", "percent"),
            ],
            [{**r.model_dump(), "margin": r.margin * 100} for r in p.departments],
        )
    )
    ctx.report.tables.append(
        table(
            "Ajustes operativos",
            [
                ("label", "Concepto"),
                ("origin", "Origen"),
                ("amount", "Efecto en resultado", "money"),
            ],
            [r.model_dump() for r in p.adjustments],
        )
    )
    journals = [r for r in ctx.records if r.kind == "journal" and r.entry_date <= ctx.end]
    balances = defaultdict(Decimal)
    period = defaultdict(Decimal)
    account_totals = defaultdict(lambda: [D(0), D(0)])
    for r in journals:
        for line in r.payload["input"]["lines"]:
            category = line["category"]
            debit = D(line["debit"])
            credit = D(line["credit"])
            balances[category] += debit - credit
            account_totals[line["account"], category][0] += debit
            account_totals[line["account"], category][1] += credit
            if ctx.start <= r.entry_date:
                period[category] += debit - credit
    revenue = -period["revenue"]
    cost = period["cost"]
    expenses = period["expense"]
    ctx.report.tables.append(
        table(
            "Resultados del libro contable USD",
            [("concept", "Concepto"), ("amount", "Monto USD", "money")],
            [
                dict(concept=k, amount=float(v))
                for k, v in [
                    ("Ingresos", revenue),
                    ("Costos", cost),
                    ("Gastos", expenses),
                    ("Resultado", revenue - cost - expenses),
                ]
            ],
        )
    )
    earnings = -(balances["revenue"] + balances["cost"] + balances["expense"])
    ctx.report.tables.append(
        table(
            "Balance de situación del libro USD",
            [("concept", "Concepto"), ("amount", "Monto USD", "money")],
            [
                dict(concept=k, amount=float(v))
                for k, v in [
                    ("Activos", balances["asset"]),
                    ("Pasivos", -balances["liability"]),
                    ("Patrimonio", -balances["equity"]),
                    ("Resultado acumulado", earnings),
                    ("Diferencia de cuadre", sum(balances.values(), D(0))),
                ]
            ],
        )
    )
    ctx.report.tables.append(
        table(
            "Balance de comprobación USD",
            [
                ("account", "Cuenta"),
                ("category", "Clasificación"),
                ("debit", "Debe", "money"),
                ("credit", "Haber", "money"),
            ],
            [
                dict(account=a, category=c, debit=float(v[0]), credit=float(v[1]))
                for (a, c), v in account_totals.items()
            ],
        )
    )
    ctx.report.tables.append(
        table(
            "Asientos y eliminaciones",
            [
                ("date", "Fecha", "date"),
                ("filial", "Filial"),
                ("description", "Concepto"),
                ("status", "Tipo", "status"),
                ("amount", "Debe = haber", "money"),
            ],
            [
                dict(
                    id=str(r.id),
                    date=r.entry_date,
                    filial=ctx.names.get(r.filial_id, "Consolidado"),
                    description=r.payload["input"]["description"],
                    status="Eliminación" if r.payload["input"]["elimination"] else "Asiento",
                    amount=float(sum(D(line["debit"]) for line in r.payload["input"]["lines"])),
                    lines=r.payload["input"]["lines"],
                )
                for r in reversed(journals)
            ],
        )
    )
    ctx.report.notes.append(
        "El libro USD se alimenta de asientos registrados: requiere saldos de apertura "
        "y contabilización. No se mezcla con el resultado operativo automático. Las "
        "eliminaciones solo aplican al consolidado. No hay sincronización automática "
        "entre libro y documentos operativos."
    )

    obligations = []
    for tax in ctx.records:
        if tax.kind != "tax":
            continue
        paid = sum(
            (
                D(p.payload["input"]["amount"])
                for p in ctx.records
                if p.kind == "tax_payment" and p.payload["input"]["obligation_id"] == str(tax.id)
            ),
            D(0),
        )
        data = tax.payload["input"]
        obligations.append(
            dict(
                filial=ctx.names[tax.filial_id],
                name=data["name"],
                currency=data["currency"],
                due=data["due_date"],
                amount=float(D(data["amount"])),
                paid=float(paid),
                balance=float(D(data["amount"]) - paid),
            )
        )
    ctx.report.tables.append(
        table(
            "Obligaciones y pasivos fiscales registrados",
            [
                ("filial", "Filial"),
                ("name", "Impuesto / provisión"),
                ("currency", "Moneda"),
                ("due", "Vencimiento", "date"),
                ("amount", "Obligación", "money"),
                ("paid", "Pagado", "money"),
                ("balance", "Saldo", "money"),
            ],
            obligations,
            "Registro de obligaciones en su moneda original. "
            "Las provisiones del libro se reconocen mediante asientos contables.",
        )
    )
