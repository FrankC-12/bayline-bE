"""Tenant-scoped control center. Money stays separated by currency; no invented FX."""

import uuid
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableEntityError
from app.modules.administracion.enums import AccountType, ExpenseCategory, MovementSourceType
from app.modules.administracion.models import (
    Account,
    ExpenseEntry,
    IncomeEntry,
)
from app.modules.filiales.models import Filial
from app.modules.holdings.control_common import TZ, D, ReportContext, value
from app.modules.holdings.control_models import HoldingRegister
from app.modules.holdings.control_schemas import ControlReport, RegisterCreate


class HoldingControlService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def filiales(self, holding_id, filial_id=None):
        rows = list(
            (
                await self.db.execute(
                    select(Filial).where(Filial.holding_id == holding_id).order_by(Filial.name)
                )
            )
            .scalars()
            .all()
        )
        if filial_id and filial_id not in {f.id for f in rows}:
            raise NotFoundError("La filial no pertenece a este holding.")
        return rows

    async def balances(self, ids, through=None):
        accounts = list(
            (
                await self.db.execute(
                    select(Account).where(Account.filial_id.in_(ids)).order_by(Account.name)
                )
            )
            .scalars()
            .all()
        )
        sums = []
        for model in (IncomeEntry, ExpenseEntry):
            query = select(model.account_id, model.currency, func.sum(model.amount)).where(
                model.filial_id.in_(ids)
            )
            if through:
                query = query.where(model.entry_date <= through)
            sums.append(
                {
                    (row[0], value(row[1])): D(row[2])
                    for row in (
                        await self.db.execute(query.group_by(model.account_id, model.currency))
                    ).all()
                }
            )
        return [
            (
                a,
                D(a.opening_balance)
                + sums[0].get((a.id, value(a.currency)), D(0))
                - sums[1].get((a.id, value(a.currency)), D(0)),
            )
            for a in accounts
        ]

    async def register(self, holding_id, actor_id, request: RegisterCreate):
        # Lock the holding so idempotency and ledger-affecting registrations serialize.
        from app.modules.holdings.models import Holding

        await self.db.execute(select(Holding.id).where(Holding.id == holding_id).with_for_update())
        existing = (
            await self.db.execute(
                select(HoldingRegister).where(
                    HoldingRegister.holding_id == holding_id,
                    HoldingRegister.request_id == request.request_id,
                )
            )
        ).scalar_one_or_none()
        payload = request.record.model_dump(mode="json")
        if existing:
            if (
                existing.entry_date != request.entry_date
                or existing.payload.get("input") != payload
            ):
                raise ConflictError("La referencia de operación ya se usó con otros datos.")
            return existing
        r = request.record
        filiales = await self.filiales(holding_id)
        ids = {f.id for f in filiales}
        filial_id = getattr(r, "filial_id", None)
        if filial_id is not None and filial_id not in ids:
            raise NotFoundError("La filial no pertenece a este holding.")
        today = datetime.now(TZ).date()
        if (
            r.kind
            in {
                "cash_count",
                "tax_payment",
                "funds_transfer",
                "journal",
                "stock_transfer",
                "stock_transfer_action",
                "vehicle_delivery",
                "risk_policy",
            }
            and request.entry_date > today
        ):
            raise UnprocessableEntityError(
                "No se puede registrar una operación realizada en una fecha futura."
            )
        evidence = {}

        async def account(account_id, owner=None):
            a = (
                await self.db.execute(
                    select(Account).where(Account.id == account_id).with_for_update()
                )
            ).scalar_one_or_none()
            if not a or a.filial_id not in ids or (owner and a.filial_id != owner):
                raise NotFoundError("La cuenta no pertenece a la filial indicada.")
            if not a.is_active:
                raise UnprocessableEntityError("La cuenta está inactiva.")
            return a

        if r.kind in {"stock_transfer", "stock_transfer_action"}:
            from app.modules.holdings.stock import stock_registration

            evidence = await stock_registration(self.db, holding_id, ids, actor_id, r)
        elif r.kind == "cash_count":
            a = await account(r.account_id, filial_id)
            if a.account_type != AccountType.CAJA:
                raise UnprocessableEntityError("El arqueo requiere una cuenta de caja.")
            balance = dict(
                (a.id, b) for a, b in await self.balances([filial_id], request.entry_date)
            )[a.id]
            evidence = dict(
                expected=str(balance),
                difference=str(r.counted - balance),
                currency=value(a.currency),
                account=a.name,
            )
        elif r.kind == "funds_transfer":
            from app.modules.administracion.service import AdministracionService

            finance = AdministracionService(self.db)
            finance._assert_open_period(request.entry_date)
            # Deterministic row locking prevents source/destination lock inversion.
            locked = {
                i: await account(i)
                for i in sorted([r.source_account_id, r.destination_account_id], key=str)
            }
            source, dest = locked[r.source_account_id], locked[r.destination_account_id]
            if source.filial_id == dest.filial_id or source.currency != dest.currency:
                raise UnprocessableEntityError(
                    "Selecciona filiales distintas y cuentas en la misma moneda."
                )
            available = dict((a.id, b) for a, b in await self.balances([source.filial_id]))[
                source.id
            ]
            if available < r.amount:
                raise UnprocessableEntityError("La cuenta de origen no tiene saldo suficiente.")
            expense = ExpenseEntry(
                filial_id=source.filial_id,
                entry_date=request.entry_date,
                category=ExpenseCategory.TRANSFERENCIA_CUENTAS,
                beneficiary=dest.name,
                description=f"Intercompañía: {r.reference}",
                amount=r.amount,
                currency=source.currency,
                account_id=source.id,
                source_type=MovementSourceType.ACCOUNT_TRANSFER,
                registered_by_user_id=actor_id,
            )
            income = IncomeEntry(
                filial_id=dest.filial_id,
                entry_date=request.entry_date,
                description=f"Intercompañía: {r.reference}",
                amount=r.amount,
                currency=dest.currency,
                account_id=dest.id,
                source_type=MovementSourceType.ACCOUNT_TRANSFER,
                registered_by_user_id=actor_id,
            )
            self.db.add(expense)
            self.db.add(income)
            await self.db.flush()
            expense.source_id = income.id
            income.source_id = expense.id
            evidence = dict(
                currency=value(source.currency),
                source_filial_id=str(source.filial_id),
                destination_filial_id=str(dest.filial_id),
                expense_id=str(expense.id),
                income_id=str(income.id),
            )
        elif r.kind == "tax_payment":
            from app.modules.administracion.service import AdministracionService

            finance = AdministracionService(self.db)
            finance._assert_open_period(request.entry_date)
            obligation = await self.db.get(HoldingRegister, r.obligation_id)
            if (
                not obligation
                or obligation.holding_id != holding_id
                or obligation.kind != "tax"
                or obligation.filial_id != filial_id
            ):
                raise NotFoundError("Obligación fiscal no encontrada.")
            if request.entry_date < obligation.entry_date:
                raise UnprocessableEntityError("El pago no puede ser anterior a la obligación.")
            a = await account(r.account_id, filial_id)
            if value(a.currency) != obligation.payload["input"]["currency"]:
                raise UnprocessableEntityError("La moneda no coincide con la obligación fiscal.")
            payments = (
                (
                    await self.db.execute(
                        select(HoldingRegister).where(
                            HoldingRegister.holding_id == holding_id,
                            HoldingRegister.kind == "tax_payment",
                        )
                    )
                )
                .scalars()
                .all()
            )
            paid = sum(
                (
                    D(p.payload["input"]["amount"])
                    for p in payments
                    if p.payload["input"]["obligation_id"] == str(r.obligation_id)
                ),
                D(0),
            )
            if paid + r.amount > D(obligation.payload["input"]["amount"]):
                raise UnprocessableEntityError("El pago supera el saldo de la obligación.")
            available = dict((a.id, b) for a, b in await self.balances([filial_id]))[a.id]
            if available < r.amount:
                raise UnprocessableEntityError("Saldo insuficiente para registrar el pago.")
            rate, usd, bs_amount = await finance._freeze_rate(
                a.currency, request.entry_date, float(r.amount)
            )
            expense = ExpenseEntry(
                exchange_rate=rate,
                amount_usd=usd,
                amount_bs=bs_amount,
                filial_id=filial_id,
                entry_date=request.entry_date,
                category=ExpenseCategory.IMPUESTOS_TASAS,
                beneficiary=obligation.payload["input"]["name"],
                description=f"Pago fiscal: {r.reference}",
                amount=r.amount,
                currency=a.currency,
                account_id=a.id,
                registered_by_user_id=actor_id,
            )
            self.db.add(expense)
            await self.db.flush()
            evidence = {"expense_id": str(expense.id)}
        elif r.kind == "vehicle_delivery":
            from app.modules.concesionario.models import DealershipVehicle, VehicleSale

            vehicle = await self.db.get(DealershipVehicle, r.vehicle_id)
            if not vehicle or vehicle.filial_id != filial_id or value(vehicle.status) != "vendido":
                raise UnprocessableEntityError(
                    "El vehículo debe estar vendido y pertenecer a la filial."
                )
            sale = (
                await self.db.execute(
                    select(VehicleSale).where(VehicleSale.vehicle_id == vehicle.id)
                )
            ).scalar_one_or_none()
            if not sale or request.entry_date < sale.created_at.date():
                raise UnprocessableEntityError(
                    "La entrega debe ser posterior a la venta registrada."
                )
            deliveries = (
                (
                    await self.db.execute(
                        select(HoldingRegister).where(
                            HoldingRegister.holding_id == holding_id,
                            HoldingRegister.kind == "vehicle_delivery",
                        )
                    )
                )
                .scalars()
                .all()
            )
            if any(e.payload["input"]["vehicle_id"] == str(vehicle.id) for e in deliveries):
                raise ConflictError("La entrega del vehículo ya fue registrada.")
            evidence = {
                "sale_id": str(sale.id),
                "vehicle": f"{vehicle.brand} {vehicle.model}",
                "vin": vehicle.vin,
            }
        elif r.kind == "journal":
            prior = (
                (
                    await self.db.execute(
                        select(HoldingRegister).where(
                            HoldingRegister.holding_id == holding_id,
                            HoldingRegister.kind == "journal",
                        )
                    )
                )
                .scalars()
                .all()
            )
            categories = {
                line["account"].strip().casefold(): line["category"]
                for entry in prior
                for line in entry.payload["input"]["lines"]
            }
            for line in r.lines:
                key = line.account.strip().casefold()
                if key in categories and categories[key] != line.category:
                    raise UnprocessableEntityError(
                        "Una cuenta contable debe conservar la misma clasificación."
                    )
                categories[key] = line.category
        elif r.kind == "opportunity" and r.opportunity_id:
            original = await self.db.get(HoldingRegister, r.opportunity_id)
            if (
                not original
                or original.holding_id != holding_id
                or original.kind != "opportunity"
                or original.filial_id != filial_id
            ):
                raise NotFoundError("Oportunidad no encontrada.")
            evidence = {
                "root_id": original.payload.get("evidence", {}).get("root_id") or str(original.id)
            }
        if r.kind == "stock_transfer":
            filial_id = uuid.UUID(evidence["source_filial_id"])
        row = HoldingRegister(
            holding_id=holding_id,
            filial_id=filial_id,
            request_id=request.request_id,
            kind=r.kind,
            entry_date=request.entry_date,
            payload={"input": payload, "evidence": evidence},
            created_by=actor_id,
            created_at=datetime.now(UTC),
        )
        self.db.add(row)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ConflictError(
                "La operación ya fue registrada; actualiza antes de continuar."
            ) from None
        await self.db.refresh(row)
        return row

    async def report(self, holding_id, section, start, end, filial_id=None):
        if start > end or (end - start).days > 366:
            raise UnprocessableEntityError("Selecciona un período válido de hasta 366 días.")
        filiales = await self.filiales(holding_id, filial_id)
        selected = [f for f in filiales if not filial_id or f.id == filial_id]
        ids = [f.id for f in selected]
        names = {f.id: f.name for f in filiales}
        report = ControlReport(
            section=section,
            generated_at=datetime.now(UTC),
            date_from=start,
            date_to=end,
            filiales=[{"id": str(f.id), "name": f.name} for f in filiales],
        )
        lower = datetime.combine(start, time.min, TZ)
        upper = datetime.combine(end + timedelta(days=1), time.min, TZ)
        records = list(
            (
                await self.db.execute(
                    select(HoldingRegister)
                    .where(
                        HoldingRegister.holding_id == holding_id,
                        HoldingRegister.kind.in_(
                            {
                                "tesoreria": ["cash_count", "tax", "tax_payment", "funds_transfer"],
                                "comercial": ["target", "opportunity"],
                                "inventario": [
                                    "stock_transfer",
                                    "stock_transfer_action",
                                    "vehicle_delivery",
                                ],
                                "taller": ["risk_policy"],
                                "contabilidad": ["journal", "tax", "tax_payment"],
                                "actividad": ["risk_policy"],
                            }.get(section, [])
                        ),
                    )
                    .order_by(HoldingRegister.created_at, HoldingRegister.id)
                )
            )
            .scalars()
            .all()
        )
        records = [
            r
            for r in records
            if not filial_id
            or r.kind == "risk_policy"
            or r.filial_id == filial_id
            or (
                r.kind in {"funds_transfer", "stock_transfer", "stock_transfer_action"}
                and str(filial_id)
                in [
                    r.payload["evidence"].get("source_filial_id"),
                    r.payload["evidence"].get("destination_filial_id"),
                ]
            )
        ]
        from importlib import import_module

        context = ReportContext(
            report=report,
            holding_id=holding_id,
            filial_id=filial_id,
            selected=selected,
            ids=ids,
            names=names,
            records=records,
            start=start,
            end=end,
            lower=lower,
            upper=upper,
        )
        # Section is a validated Literal from the public schema, never a user-controlled module
        # name.
        if section not in {
            "tesoreria",
            "comercial",
            "inventario",
            "taller",
            "contabilidad",
            "actividad",
        }:
            raise UnprocessableEntityError("Área de holding desconocida.")
        renderer = import_module(f"app.modules.holdings.reports.{section}")
        await renderer.render(self, context)
        return report
