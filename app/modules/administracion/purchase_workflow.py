"""One purchase: physical receipt, invoice reconciliation, then partial payments."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.core.idempotency import remember_operation, replay_operation
from app.modules.administracion.enums import (
    AccountCurrency,
    CounterpartyType,
    ExpenseCategory,
    MovementSourceType,
    PurchaseRequestStatus,
)
from app.modules.administracion.exceptions import PurchaseRequestNotFoundError
from app.modules.administracion.models import Account, ExpenseEntry, PurchaseRequest
from app.modules.administracion.service import AdministracionService
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part
from app.modules.warehouse.models import PartLot
from app.modules.warehouse.schemas import LotLineInput
from app.modules.warehouse.service import AlmacenService


def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class PurchaseWorkflow:
    def __init__(self, db):
        self.db = db

    async def scope(self, request_id):
        filial_id = (
            await self.db.execute(
                select(PurchaseRequest.filial_id).where(PurchaseRequest.id == request_id)
            )
        ).scalar_one_or_none()
        if filial_id is None:
            raise PurchaseRequestNotFoundError(str(request_id))
        return filial_id

    async def lock(self, request_id):
        filial_id = (
            await self.db.execute(
                select(PurchaseRequest.filial_id).where(PurchaseRequest.id == request_id)
            )
        ).scalar_one_or_none()
        if filial_id is None:
            raise PurchaseRequestNotFoundError(str(request_id))
        await self.db.execute(select(Filial.id).where(Filial.id == filial_id).with_for_update())
        request = (
            await self.db.execute(
                select(PurchaseRequest)
                .where(PurchaseRequest.id == request_id)
                .options(selectinload(PurchaseRequest.lines))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if request is None:
            raise PurchaseRequestNotFoundError(str(request_id))
        return request

    async def activity(self, request, action, user_id, **fields):
        from app.modules.users.models import User

        user = await self.db.get(User, user_id) if user_id else None
        request.activity = [
            *(request.activity or []),
            dict(
                action=action,
                user_id=str(user_id) if user_id else None,
                user_name=user.full_name if user else None,
                occurred_at=datetime.now(UTC).isoformat(),
                **fields,
            ),
        ]

    async def receive(self, request_id, payload, user_id):
        try:
            filial_id = await self.scope(request_id)
            replay = await replay_operation(
                self.db,
                payload,
                filial_id,
                "purchase_receive",
                user_id,
                resource_id=request_id,
            )
            if replay is not None:
                return await AdministracionService(self.db).get_request(request_id)
            request = await self.lock(request_id)
            if request.status not in {
                PurchaseRequestStatus.ENVIADA,
                PurchaseRequestStatus.COTIZADA,
                PurchaseRequestStatus.PAGADA,
            }:
                raise ConflictError("Esta compra ya fue recibida o no admite recepción.")
            by_id = {line.id: line for line in request.lines}
            if len({x.line_id for x in payload.lines}) != len(payload.lines) or any(
                x.line_id not in by_id for x in payload.lines
            ):
                raise BadRequestError("Las líneas deben pertenecer a la compra y no repetirse.")
            if not any(x.quantity for x in payload.lines):
                raise BadRequestError("Registra al menos una unidad recibida.")
            warehouse = AlmacenService(self.db)
            await warehouse.ensure_inventory_scope(
                request.filial_id,
                [payload.warehouse_id],
                [by_id[x.line_id].part_id for x in payload.lines],
            )
            await self.db.execute(
                select(Part.id)
                .where(
                    Part.id.in_(sorted({by_id[x.line_id].part_id for x in payload.lines}, key=str))
                )
                .order_by(Part.id)
                .with_for_update()
            )
            for item in payload.lines:
                line = by_id[item.line_id]
                if item.quantity > line.quantity - line.received_quantity:
                    raise BadRequestError("La recepción supera la cantidad pendiente.")
                if not item.quantity:
                    continue
                if line.unit_cost is None:
                    raise BadRequestError("Cotiza el costo de cada repuesto antes de recibirlo.")
                lot = await warehouse._create_single_lot(
                    request.filial_id,
                    payload.warehouse_id,
                    LotLineInput(
                        part_id=line.part_id,
                        quantity=item.quantity,
                        unit_cost=float(line.unit_cost),
                        purchase_request_id=request.id,
                        location=item.location,
                    ),
                    note=f"Compra {request.code}",
                    responsible_user_id=user_id,
                )
                await self.db.flush()
                line.received_quantity += item.quantity
                line.receipts = [
                    *(line.receipts or []),
                    dict(
                        lot_id=str(lot.id),
                        lot_code=lot.code,
                        quantity=item.quantity,
                        location=item.location,
                    ),
                ]
                await self.activity(
                    request,
                    "received",
                    user_id,
                    lot_id=str(lot.id),
                    lot_code=lot.code,
                    quantity=item.quantity,
                )
            request.warehouse_id = payload.warehouse_id
            if all(line.received_quantity == line.quantity for line in request.lines):
                request.status = PurchaseRequestStatus.RECIBIDA
            remember_operation(
                self.db,
                payload,
                request.filial_id,
                "purchase_receive",
                user_id,
                {"received": True},
                resource_id=request_id,
            )
            await self.db.commit()
            return await AdministracionService(self.db).get_request(request_id)
        except Exception:
            await self.db.rollback()
            raise

    async def legacy_paid_amount(self, request):
        """Recover only amounts evidenced by the historical linked expense."""
        if not request.paid_at or request.paid_amount_usd:
            return money(request.paid_amount_usd or 0)
        expense = (
            await self.db.get(ExpenseEntry, request.payment_expense_entry_id)
            if request.payment_expense_entry_id
            else None
        )
        if expense is None or expense.filial_id != request.filial_id:
            raise ConflictError(
                "Revisa el pago anterior de esta compra en Finanzas antes de conciliar."
            )
        amount = (
            expense.amount_usd
            if expense.amount_usd is not None
            else expense.amount if expense.currency == AccountCurrency.USD else None
        )
        if amount is None:
            raise ConflictError(
                "El pago anterior no tiene equivalente USD registrado. Revisa su conciliación en Finanzas."
            )
        linked = (
            (
                await self.db.execute(
                    select(PurchaseRequest)
                    .where(PurchaseRequest.payment_expense_entry_id == expense.id)
                    .options(selectinload(PurchaseRequest.lines))
                )
            )
            .scalars()
            .all()
        )
        if len(linked) == 1:
            return money(amount)
        totals = {
            item.id: money(sum(line.quantity * float(line.unit_cost or 0) for line in item.lines))
            for item in linked
        }
        if money(sum(totals.values())) == money(amount):
            return totals[request.id]
        raise ConflictError(
            "El pago anterior agrupa varias compras sin un reparto registrado. Revisa ese pago en Finanzas antes de conciliar."
        )

    async def reconcile(self, request_id, payload, user_id):
        try:
            request = await self.lock(request_id)
            prior_paid = await self.legacy_paid_amount(request)
            if request.invoice_number:
                raise ConflictError("Esta compra ya tiene factura conciliada.")
            if request.status not in {
                PurchaseRequestStatus.RECIBIDA,
                PurchaseRequestStatus.CONCILIADA,
            }:
                raise ConflictError("Recibe toda la mercancía antes de conciliar la factura.")
            lines = {x.line_id: x for x in payload.lines}
            if len(lines) != len(payload.lines) or set(lines) != {x.id for x in request.lines}:
                raise BadRequestError("Incluye exactamente todas las líneas de la compra.")
            if not payload.invoice_number.strip() or not payload.invoice_control_number.strip():
                raise BadRequestError("Indica factura y control.")
            subtotal = Decimal(0)
            for line in request.lines:
                line.invoiced_unit_cost = money(lines[line.id].unit_cost)
                subtotal += line.received_quantity * money(line.invoiced_unit_cost)
                lots = (
                    (
                        await self.db.execute(
                            select(PartLot)
                            .where(
                                PartLot.purchase_request_id == request.id,
                                PartLot.part_id == line.part_id,
                            )
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
                # Transfer descendants retain the supplier reference and receive the reconciled cost.
                for lot in lots:
                    lot.unit_cost = line.invoiced_unit_cost
            request.invoice_number = payload.invoice_number.strip()
            request.invoice_control_number = payload.invoice_control_number.strip()
            request.invoice_date = payload.invoice_date
            request.credit_days = payload.credit_days
            request.due_date = payload.invoice_date + timedelta(days=payload.credit_days)
            request.invoice_iva_percentage = payload.iva_percentage
            request.invoice_total = money(
                subtotal + money(subtotal * Decimal(str(payload.iva_percentage)) / 100)
            )
            if prior_paid > money(request.invoice_total):
                raise ConflictError(
                    "La factura es menor que los pagos anteriores. Revisa el saldo en Finanzas."
                )
            request.paid_amount_usd = prior_paid
            if prior_paid < money(request.invoice_total):
                request.paid_at = None
            elif money(request.invoice_total) == 0:
                request.paid_at = datetime.now(UTC)
            request.status = PurchaseRequestStatus.CONCILIADA
            request.conciliated_at = datetime.now(UTC)
            await self.activity(
                request,
                "reconciled",
                user_id,
                invoice_number=request.invoice_number,
                total=float(request.invoice_total),
            )
            await self.db.commit()
            return await AdministracionService(self.db).get_request(request_id)
        except Exception:
            await self.db.rollback()
            raise

    async def pay(self, request_id, payload, user_id):
        try:
            filial_id = await self.scope(request_id)
            replay = await replay_operation(
                self.db,
                payload,
                filial_id,
                "purchase_payment",
                user_id,
                resource_id=request_id,
            )
            if replay is not None:
                return await AdministracionService(self.db).get_request(request_id)
            request = await self.lock(request_id)
            if not request.invoice_number or request.invoice_total is None:
                raise ConflictError("Concilia la factura antes de registrar un pago.")
            if request.paid_at:
                raise ConflictError("La factura ya está pagada.")
            account = await self.db.get(Account, payload.account_id)
            if account is None or account.filial_id != request.filial_id:
                raise ForbiddenError("La cuenta debe pertenecer a la filial.")
            if not account.is_active or account.currency.value != payload.currency:
                raise BadRequestError(
                    "La cuenta no está activa o no corresponde a la moneda elegida."
                )
            AdministracionService(self.db)._assert_open_period(payload.entry_date)
            if payload.currency == "bs" and not payload.exchange_rate:
                raise BadRequestError("Indica una tasa para el pago en bolívares.")
            rate = Decimal(str(payload.exchange_rate)) if payload.currency == "bs" else None
            amount_usd = (
                money(Decimal(str(payload.amount)) / rate) if rate else money(payload.amount)
            )
            if amount_usd <= 0:
                raise BadRequestError("El monto es demasiado pequeño.")
            balance = money(request.invoice_total) - money(request.paid_amount_usd or 0)
            if amount_usd > balance:
                raise ConflictError("El pago supera el saldo pendiente.")
            expense = ExpenseEntry(
                filial_id=request.filial_id,
                entry_date=payload.entry_date,
                category=ExpenseCategory.COMPRAS_PROVEEDORES,
                beneficiary=(
                    await AdministracionService(self.db).get_supplier(request.supplier_id)
                ).business_name,
                source_type=MovementSourceType.PURCHASE_REQUEST,
                source_id=request.id,
                description=f"Pago {request.code} · Factura {request.invoice_number}",
                amount=payload.amount,
                currency=AccountCurrency(payload.currency),
                account_id=payload.account_id,
                counterparty_type=CounterpartyType.PROVEEDOR,
                counterparty_supplier_id=request.supplier_id,
                reference=payload.reference,
                exchange_rate=rate,
                amount_usd=amount_usd,
                amount_bs=money(payload.amount) if rate else None,
                registered_by_user_id=user_id,
            )
            self.db.add(expense)
            await self.db.flush()
            request.paid_amount_usd = money(request.paid_amount_usd or 0) + amount_usd
            request.payment_account_id = account.id
            request.payment_expense_entry_id = expense.id
            request.paid_by_user_id = user_id
            if money(request.paid_amount_usd) == money(request.invoice_total):
                request.paid_at = datetime.now(UTC)
            await self.activity(
                request,
                "payment",
                user_id,
                expense_id=str(expense.id),
                amount_usd=float(amount_usd),
                balance_usd=float(balance - amount_usd),
            )
            remember_operation(
                self.db,
                payload,
                request.filial_id,
                "purchase_payment",
                user_id,
                {"expense_id": str(expense.id)},
                resource_id=request_id,
            )
            await self.db.commit()
            return await AdministracionService(self.db).get_request(request_id)
        except Exception:
            await self.db.rollback()
            raise
