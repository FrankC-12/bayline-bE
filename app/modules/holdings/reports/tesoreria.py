"""Holding tesoreria report."""

import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select

from app.modules.administracion.models import (
    ExpenseEntry,
    IncomeEntry,
    PurchaseRequest,
    Supplier,
)
from app.modules.holdings.control_common import TZ, D, ReportContext, aware, table, value


async def render(self, ctx: ReportContext):
    balances = await self.balances(ctx.ids)
    totals = defaultdict(Decimal)
    for a, b in balances:
        if a.is_active:
            totals[value(a.currency)] += b
    for c in ("usd", "bs", "eur"):
        ctx.metric(
            f"Disponible {c.upper()}", totals[c], "money", c, "Cuentas activas; saldo actual"
        )
    ctx.report.tables.append(
        table(
            "Cuentas y disponibilidad",
            [
                ("filial", "Filial"),
                ("name", "Cuenta"),
                ("bank", "Banco"),
                ("currency", "Moneda"),
                ("balance", "Saldo", "money"),
                ("status", "Estado", "status"),
            ],
            [
                dict(
                    id=str(a.id),
                    filial=ctx.names[a.filial_id],
                    filial_id=str(a.filial_id),
                    name=a.name,
                    bank=a.bank or "Caja",
                    account_type=value(a.account_type),
                    currency=value(a.currency),
                    balance=float(b),
                    status="activa" if a.is_active else "inactiva",
                )
                for a, b in balances
            ],
        )
    )
    movements = []
    flows = defaultdict(lambda: [D(0), D(0)])
    for model, sign in ((IncomeEntry, 0), (ExpenseEntry, 1)):
        grouped = (
            await self.db.execute(
                select(model.filial_id, model.currency, func.sum(model.amount))
                .where(model.filial_id.in_(ctx.ids), model.entry_date.between(ctx.start, ctx.end))
                .group_by(model.filial_id, model.currency)
            )
        ).all()
        for f, c, amount in grouped:
            flows[f, value(c)][sign] = D(amount)
        rows = (
            (
                await self.db.execute(
                    select(model)
                    .where(
                        model.filial_id.in_(ctx.ids), model.entry_date.between(ctx.start, ctx.end)
                    )
                    .order_by(model.created_at.desc())
                    .limit(150)
                )
            )
            .scalars()
            .all()
        )
        movements.extend(
            dict(
                id=str(x.id),
                filial=ctx.names[x.filial_id],
                date=x.entry_date,
                description=x.description,
                currency=value(x.currency),
                amount=float(x.amount),
                type="Ingreso" if sign == 0 else "Egreso",
                created_at=x.created_at,
            )
            for x in rows
        )
    ctx.report.tables.append(
        table(
            "Flujo del período por filial y moneda",
            [
                ("filial", "Filial"),
                ("currency", "Moneda"),
                ("income", "Entradas", "money"),
                ("expense", "Salidas", "money"),
                ("net", "Flujo neto", "money"),
            ],
            [
                dict(
                    filial=ctx.names[f],
                    currency=c,
                    income=float(v[0]),
                    expense=float(v[1]),
                    net=float(v[0] - v[1]),
                )
                for (f, c), v in flows.items()
            ],
            "Incluye transferencias entre cuentas; no representa ventas ni utilidad.",
        )
    )
    ctx.report.tables.append(
        table(
            "Movimientos recientes",
            [
                ("date", "Fecha", "date"),
                ("filial", "Filial"),
                ("type", "Tipo"),
                ("description", "Descripción"),
                ("currency", "Moneda"),
                ("amount", "Monto", "money"),
            ],
            sorted(movements, key=lambda x: aware(x["created_at"]), reverse=True)[:150],
            "Últimos 150 movimientos del período.",
        )
    )
    purchases = (
        await self.db.execute(
            select(PurchaseRequest, Supplier.business_name)
            .join(Supplier, Supplier.id == PurchaseRequest.supplier_id)
            .where(
                PurchaseRequest.filial_id.in_(ctx.ids),
                PurchaseRequest.invoice_total.is_not(None),
                PurchaseRequest.status != "CANCELADA",
            )
        )
    ).all()
    payables = [
        dict(
            id=str(p.id),
            filial=ctx.names[p.filial_id],
            document=p.code,
            description=s,
            due=p.due_date,
            amount=float(max(D(p.invoice_total) - D(p.paid_amount_usd), D(0))),
            currency="usd",
            status=(
                "Vencida" if p.due_date and p.due_date < datetime.now(TZ).date() else "Por pagar"
            ),
        )
        for p, s in purchases
        if D(p.invoice_total) > D(p.paid_amount_usd)
    ]
    taxes = [r for r in ctx.records if r.kind == "tax"]
    for tax in taxes:
        inp = tax.payload["input"]
        paid = sum(
            (
                D(r.payload["input"]["amount"])
                for r in ctx.records
                if r.kind == "tax_payment" and r.payload["input"]["obligation_id"] == str(tax.id)
            ),
            D(0),
        )
        balance = D(inp["amount"]) - paid
        if balance > 0:
            payables.append(
                dict(
                    id=str(tax.id),
                    filial_id=str(tax.filial_id),
                    filial=ctx.names[tax.filial_id],
                    document="Fiscal",
                    description=inp["name"],
                    due=inp["due_date"],
                    amount=float(balance),
                    currency=inp["currency"],
                    status="Por pagar",
                )
            )
    payables.sort(key=lambda x: str(x["due"] or "9999"))
    ctx.report.tables.append(
        table(
            "Calendario global de pagos",
            [
                ("due", "Vencimiento", "date"),
                ("filial", "Filial"),
                ("document", "Documento"),
                ("description", "Proveedor / obligación"),
                ("currency", "Moneda"),
                ("amount", "Saldo", "money"),
                ("status", "Estado", "status"),
            ],
            payables,
        )
    )
    ctx.report.tables.append(
        table(
            "Arqueos de caja",
            [
                ("date", "Fecha", "date"),
                ("filial", "Filial"),
                ("account", "Caja"),
                ("currency", "Moneda"),
                ("expected", "Saldo registrado", "money"),
                ("counted", "Contado", "money"),
                ("difference", "Diferencia", "money"),
            ],
            [
                dict(
                    date=r.entry_date,
                    filial=ctx.names[r.filial_id],
                    **r.payload["evidence"],
                    counted=r.payload["input"]["counted"],
                )
                for r in ctx.records
                if r.kind == "cash_count" and ctx.start <= r.entry_date <= ctx.end
            ],
        )
    )
    ctx.report.tables.append(
        table(
            "Transferencias intercompañía",
            [
                ("date", "Fecha", "date"),
                ("origin", "Origen"),
                ("destination", "Destino"),
                ("currency", "Moneda"),
                ("amount", "Monto", "money"),
                ("reference", "Referencia"),
            ],
            [
                dict(
                    date=r.entry_date,
                    origin=ctx.names.get(uuid.UUID(r.payload["evidence"]["source_filial_id"])),
                    destination=ctx.names.get(
                        uuid.UUID(r.payload["evidence"]["destination_filial_id"])
                    ),
                    currency=r.payload["evidence"]["currency"],
                    amount=r.payload["input"]["amount"],
                    reference=r.payload["input"]["reference"],
                )
                for r in ctx.records
                if r.kind == "funds_transfer" and ctx.start <= r.entry_date <= ctx.end
            ],
        )
    )
