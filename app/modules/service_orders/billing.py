"""Invoice issuance and payment capture share one locked database transaction."""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.modules.administracion.enums import AccountCurrency, IncomeSource
from app.modules.administracion.models import Account, IncomeEntry
from app.modules.clients.models import Client, Vehicle
from app.modules.exchange_rates.models import ExchangeRate
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part
from app.modules.post_ventas.models import LaborSettings
from app.modules.service_orders.billing_schemas import BillingInput, BillingQuote, InvoiceCreate
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.guards import require_editable_order
from app.modules.service_orders.models import ServiceOrderInvoice
from app.modules.service_orders.service import ServiceOrderService


def billing_day():
    return datetime.now(ZoneInfo("America/Caracas")).date()


def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def calculate_payment(base, method, usd_base, igtf_percentage, rate):
    base = money(base)
    if method == "usd":
        usd_base = base
    elif method == "bs":
        usd_base = Decimal(0)
    elif not Decimal(0) < usd_base < base:
        raise BadRequestError(
            "En pago mixto, el aporte USD debe ser mayor a cero y menor al total sin IGTF."
        )
    if method != "mixed" and usd_base < 0:
        raise BadRequestError("Monto USD inválido.")
    bs_base = base - usd_base
    if bs_base > 0 and (rate is None or rate <= 0):
        raise BadRequestError(
            "No hay tasa BCV del día. Actualiza la tasa antes de cobrar en Bs.",
            error_code="bcv_rate_required",
        )
    igtf = money(usd_base * Decimal(str(igtf_percentage)) / 100)
    return dict(
        usd_base=float(usd_base),
        bs_base_usd=float(bs_base),
        igtf_amount=float(igtf),
        due_usd=float(usd_base + igtf),
        due_bs=float(money(bs_base * rate)) if bs_base else 0.0,
        total_usd=float(base + igtf),
    )


class BillingService:
    def __init__(self, db):
        self.db = db
        self.orders = ServiceOrderService(db)

    async def context(self, order_id):
        order = await self.orders.get_order(order_id)
        settings = (
            await self.db.execute(
                select(LaborSettings).where(LaborSettings.filial_id == order.filial_id)
            )
        ).scalar_one_or_none()
        rate = (
            await self.db.execute(
                select(ExchangeRate).where(
                    ExchangeRate.currency == "USD", ExchangeRate.value_date == billing_day()
                )
            )
        ).scalar_one_or_none()
        accounts = (
            (
                await self.db.execute(
                    select(Account)
                    .where(
                        Account.filial_id == order.filial_id,
                        Account.is_active.is_(True),
                        Account.currency.in_([AccountCurrency.USD, AccountCurrency.BS]),
                    )
                    .order_by(Account.name)
                )
            )
            .scalars()
            .all()
        )
        return dict(
            summary=(await self.orders.get_order_summary(order_id)).model_dump(mode="json"),
            igtf_percentage=float(settings.igtf_percentage) if settings else 3.0,
            bcv_rate=float(rate.rate_ves) if rate else None,
            bcv_date=rate.value_date.isoformat() if rate else None,
            accounts=[dict(id=str(a.id), name=a.name, currency=a.currency.value) for a in accounts],
        )

    async def quote(self, order_id, payload: BillingInput):
        order = await self.orders.get_order(order_id)
        if order.status != ServiceOrderStatus.COMPLETADO or order.invoiced_at is not None:
            raise ConflictError("La orden debe estar completada y sin facturar.")
        context = await self.context(order_id)
        summary = context["summary"]
        result = dict(
            summary=summary,
            payment_method=payload.payment_method,
            billing_date=billing_day().isoformat(),
            bcv_rate=context["bcv_rate"],
            bcv_date=context["bcv_date"],
            igtf_percentage=context["igtf_percentage"],
        )
        result.update(
            calculate_payment(
                summary["total"],
                payload.payment_method,
                payload.usd_base,
                context["igtf_percentage"],
                Decimal(str(context["bcv_rate"])) if context["bcv_rate"] else None,
            )
        )
        result["quote_hash"] = digest(result)
        return BillingQuote.model_validate(result)

    async def get_invoice(self, order_id):
        invoice = (
            await self.db.execute(
                select(ServiceOrderInvoice).where(ServiceOrderInvoice.service_order_id == order_id)
            )
        ).scalar_one_or_none()
        if invoice is None:
            raise NotFoundError("Esta orden no tiene un documento de factura registrado.")
        return invoice

    async def issue(self, order_id, payload: InvoiceCreate, user_id):
        try:
            # Permit retries after invoicing, but never modify an existing invoice.
            from app.modules.service_orders.models import ServiceOrder

            order = (
                await self.db.execute(
                    select(ServiceOrder)
                    .where(ServiceOrder.id == order_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if order is None:
                raise NotFoundError("Orden no encontrada.")
            request_hash = digest(payload.model_dump(mode="json"))
            existing = (
                await self.db.execute(
                    select(ServiceOrderInvoice).where(
                        ServiceOrderInvoice.service_order_id == order_id
                    )
                )
            ).scalar_one_or_none()
            if existing:
                if (
                    existing.request_id == payload.request_id
                    and existing.request_hash == request_hash
                ):
                    return existing
                raise ConflictError("Esta orden ya fue facturada.", error_code="already_invoiced")
            await require_editable_order(self.db, order_id)
            quote = await self.quote(order_id, payload)
            if quote.quote_hash != payload.quote_hash:
                raise ConflictError(
                    "Los importes o la tasa cambiaron. Revisa el cobro actualizado.",
                    error_code="billing_quote_changed",
                )
            if payload.paid_usd != money(quote.due_usd) or payload.paid_bs != money(quote.due_bs):
                raise BadRequestError(
                    "El pago recibido debe coincidir con los importes a cobrar.",
                    error_code="payment_mismatch",
                )
            payments = []
            for currency, amount, account_id in (
                (AccountCurrency.USD, payload.paid_usd, payload.usd_account_id),
                (AccountCurrency.BS, payload.paid_bs, payload.bs_account_id),
            ):
                if amount == 0:
                    continue
                account = (
                    await self.db.execute(
                        select(Account)
                        .where(Account.id == account_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if (
                    account is None
                    or not account.is_active
                    or account.filial_id != order.filial_id
                    or account.currency != currency
                ):
                    raise BadRequestError(
                        f"Selecciona una cuenta activa en {currency.value.upper()} de la filial."
                    )
                payments.append(
                    dict(
                        currency=currency.value,
                        amount=float(amount),
                        account_id=str(account.id),
                        account_name=account.name,
                    )
                )
            now = datetime.now(UTC)
            invoice_id = uuid.uuid4()
            code = f"FAC-{order.code}"
            vehicle = await self.db.get(Vehicle, order.vehicle_id)
            client = await self.db.get(Client, vehicle.client_id) if vehicle else None
            filial = await self.db.get(Filial, order.filial_id)
            if vehicle is None or client is None or filial is None:
                raise BadRequestError(
                    "Completa los datos del cliente, vehículo y filial antes de facturar."
                )
            document = quote.model_dump(mode="json")
            part_ids = {
                line.part_id for transfer in quote.summary.transfers for line in transfer.lines
            }
            parts = (
                (await self.db.execute(select(Part).where(Part.id.in_(part_ids)))).scalars().all()
                if part_ids
                else []
            )
            document.update(
                id=str(invoice_id),
                code=code,
                order_code=order.code,
                issued_at=now.isoformat(),
                filial_name=filial.name,
                client_name=client.full_name,
                client_document=f"{client.document_type.value}-{client.document_number}",
                client_address=client.address,
                vehicle=f"{vehicle.brand} {vehicle.model} · {vehicle.plate}",
                parts={str(p.id): dict(name=p.name, code=p.code) for p in parts},
                payments=payments,
                payment_reference=payload.payment_reference,
            )
            invoice = ServiceOrderInvoice(
                id=invoice_id,
                service_order_id=order.id,
                request_id=payload.request_id,
                request_hash=request_hash,
                code=code,
                issued_at=now,
                issued_by_user_id=user_id,
                total_usd=money(quote.total_usd),
                document=document,
            )
            self.db.add(invoice)
            for payment in payments:
                self.db.add(
                    IncomeEntry(
                        filial_id=order.filial_id,
                        entry_date=billing_day(),
                        source=IncomeSource.AUTOMATICO,
                        origin_reference=code,
                        description=f"Cobro {code}",
                        amount=payment["amount"],
                        currency=payment["currency"],
                        account_id=uuid.UUID(payment["account_id"]),
                        registered_by_user_id=user_id,
                    )
                )
            quote.summary.pricing_frozen = True
            quote.summary.igtf_percentage = quote.igtf_percentage
            quote.summary.igtf_amount = quote.igtf_amount
            quote.summary.total = quote.total_usd
            order.pricing_snapshot = quote.summary.model_dump(mode="json")
            order.total_amount = money(quote.total_usd)
            order.invoiced_at = now
            await self.db.commit()
            return invoice
        except IntegrityError as exc:
            await self.db.rollback()
            raise ConflictError("La factura o la solicitud de cobro ya existe.") from exc
        except Exception:
            await self.db.rollback()
            raise
