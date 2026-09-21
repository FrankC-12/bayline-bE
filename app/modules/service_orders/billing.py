"""Invoice issuance and payment capture share one locked database transaction."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.venezuela_time import aging_bucket, venezuela_today
from app.modules.administracion.enums import AccountCurrency, IncomeSource, MovementSourceType
from app.modules.administracion.models import Account, IncomeEntry
from app.modules.clients.models import Client, Vehicle
from app.modules.exchange_rates.models import ExchangeRate
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part
from app.modules.post_ventas.enums import WorkshopWarrantyCoverage
from app.modules.post_ventas.models import LaborSettings, WorkshopWarranty
from app.modules.service_orders.billing_schemas import (
    BillingInput,
    BillingQuote,
    CollectInvoicePaymentInput,
    HoldingWarrantyFilialReceivables,
    HoldingWarrantyReceivablesReport,
    InvoiceCreate,
    ReceivableRead,
)
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.exceptions import (
    InvoiceAlreadyCollectedError,
    InvoiceNotFoundError,
    ReceivableCurrencyMismatchError,
)
from app.modules.service_orders.guards import require_editable_order
from app.modules.service_orders.models import (
    ServiceOrder,
    ServiceOrderInvoice,
    ServiceOrderTask,
    ServiceOrderTransferLine,
)
from app.modules.service_orders.service import ServiceOrderService


def billing_day():
    return venezuela_today()


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
            iva_retention_default_percentage=float(settings.iva_retention_default_percentage) if settings else 0.0,
            islr_retention_default_percentage=float(settings.islr_retention_default_percentage) if settings else 0.0,
            bcv_rate=float(rate.rate_ves) if rate else None,
            bcv_date=rate.value_date.isoformat() if rate else None,
            accounts=[dict(id=str(a.id), name=a.name, currency=a.currency.value) for a in accounts],
        )

    async def _resolve_billed_client(
        self, order, billed_client_id: uuid.UUID | None, billed_supplier_id: uuid.UUID | None = None
    ) -> Client | None:
        """None/None means "bill the vehicle's own owner" — resolved later,
        once the vehicle is loaded. billed_supplier_id bills a Supplier
        instead, via its linked billing Client (created on first use)."""
        if billed_supplier_id is not None:
            from app.modules.clients.service import ClientService

            return await ClientService(self.db).get_or_create_supplier_billing_client(
                order.filial_id, billed_supplier_id
            )
        if billed_client_id is None:
            return None
        client = await self.db.get(Client, billed_client_id)
        if client is None or client.filial_id != order.filial_id:
            raise BadRequestError("El cliente elegido para facturar no pertenece a esta filial.")
        return client

    async def quote(self, order_id, payload: BillingInput):
        order = await self.orders.get_order(order_id)
        if order.status != ServiceOrderStatus.COMPLETADO or order.invoiced_at is not None:
            raise ConflictError("La orden debe estar completada y sin facturar.")
        await self._resolve_billed_client(order, payload.billed_client_id, payload.billed_supplier_id)
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
        # IVA retention is a % of the IVA line itself; ISLR retention is a %
        # of the pre-tax (IVA-exclusive) subtotal — the standard bases for
        # each, per Venezuelan withholding practice. Both default to 0 for a
        # client that isn't a withholding agent.
        iva_retention_amount = money(Decimal(str(summary["iva_amount"])) * payload.iva_retention_percentage / 100)
        pretax_base = Decimal(str(summary["parts_subtotal"])) + Decimal(str(summary["labor_subtotal"]))
        islr_retention_amount = money(pretax_base * payload.islr_retention_percentage / 100)
        result.update(
            iva_retention_percentage=float(payload.iva_retention_percentage),
            iva_retention_amount=float(iva_retention_amount),
            islr_retention_percentage=float(payload.islr_retention_percentage),
            islr_retention_amount=float(islr_retention_amount),
            net_expected=float(money(Decimal(str(result["total_usd"])) - iva_retention_amount - islr_retention_amount)),
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
            if payload.paid_usd > money(quote.due_usd) or payload.paid_bs > money(quote.due_bs):
                raise BadRequestError(
                    "El pago recibido no puede superar los importes a cobrar.",
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
            filial = await self.db.get(Filial, order.filial_id)
            client = await self._resolve_billed_client(order, payload.billed_client_id, payload.billed_supplier_id)
            if client is None:
                client = await self.db.get(Client, vehicle.client_id) if vehicle else None
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
                vehicle=f"{vehicle.brand} {vehicle.model} · {vehicle.plate or 'Sin placa'}",
                parts={str(p.id): dict(name=p.name, code=p.code) for p in parts},
                payments=payments,
                payment_reference=payload.payment_reference,
            )
            paid_bs_as_usd = (
                (payload.paid_bs / Decimal(str(quote.bcv_rate)))
                if quote.bcv_rate and payload.paid_bs > 0
                else Decimal(0)
            )
            amount_paid_at_issuance = money(payload.paid_usd + paid_bs_as_usd)
            total_usd = money(quote.total_usd)
            net_expected = money(quote.net_expected)
            # A withheld amount is never collected as cash — an invoice is
            # fully settled once cash-in-hand covers what's left after
            # retentions, not the nominal total.
            fully_paid = amount_paid_at_issuance >= net_expected
            billed_someone_else = client.id != (vehicle.client_id if vehicle else None)
            invoice = ServiceOrderInvoice(
                id=invoice_id,
                service_order_id=order.id,
                request_id=payload.request_id,
                request_hash=request_hash,
                code=code,
                issued_at=now,
                issued_by_user_id=user_id,
                total_usd=total_usd,
                document=document,
                billed_client_id=client.id,
                client_confirmed_at=now if (billed_someone_else and payload.client_confirmed) else None,
                client_confirmed_note=payload.client_confirmed_note if billed_someone_else else None,
                iva_retention_percentage=quote.iva_retention_percentage or None,
                iva_retention_amount=money(quote.iva_retention_amount) if quote.iva_retention_amount else None,
                islr_retention_percentage=quote.islr_retention_percentage or None,
                islr_retention_amount=money(quote.islr_retention_amount) if quote.islr_retention_amount else None,
                amount_paid_at_issuance=amount_paid_at_issuance,
                collected_at=now if fully_paid else None,
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
                        source_type=MovementSourceType.SERVICE_ORDER,
                        source_id=order.id,
                    )
                )
            quote.summary.pricing_frozen = True
            quote.summary.igtf_percentage = quote.igtf_percentage
            quote.summary.igtf_amount = quote.igtf_amount
            quote.summary.total = quote.total_usd
            order.pricing_snapshot = quote.summary.model_dump(mode="json")
            order.total_amount = money(quote.total_usd)
            order.invoiced_at = now

            # Workshop warranties, created automatically the instant payment
            # is confirmed — no advisor step. Every task gets a "mano de
            # obra" warranty; a task that also installed a part additionally
            # gets a "repuesto" one, since an install fails fast and a part
            # wears out slowly — each runs on its own configured term.
            # Skipped entirely (not blocking) when the vehicle has no VIN,
            # same convention used elsewhere for VIN-dependent warranties.
            if vehicle is not None and vehicle.vin:
                settings_result = await self.db.execute(
                    select(LaborSettings).where(LaborSettings.filial_id == order.filial_id)
                )
                settings = settings_result.scalar_one_or_none()
                labor_days = settings.workshop_warranty_days if settings else 90
                labor_km = settings.workshop_warranty_km if settings else 5000
                parts_days = settings.workshop_parts_warranty_days if settings else 90
                parts_km = settings.workshop_parts_warranty_km if settings else 5000

                tasks = list(
                    (
                        await self.db.execute(
                            select(ServiceOrderTask).where(ServiceOrderTask.service_order_id == order.id)
                        )
                    ).scalars()
                )
                task_ids_with_parts: set[uuid.UUID] = set()
                if tasks:
                    rows = await self.db.execute(
                        select(ServiceOrderTransferLine.service_order_task_id)
                        .where(ServiceOrderTransferLine.service_order_task_id.in_([t.id for t in tasks]))
                        .distinct()
                    )
                    task_ids_with_parts = {row[0] for row in rows if row[0] is not None}

                starts_at = now.date()

                def _workshop_warranty(task, coverage, days, km):
                    return WorkshopWarranty(
                        filial_id=order.filial_id,
                        vin=vehicle.vin,
                        service_order_id=order.id,
                        service_order_task_id=task.id,
                        coverage_type=coverage,
                        tempario_code_snapshot=task.code_snapshot,
                        tempario_name_snapshot=task.name_snapshot,
                        technician_user_id=order.technician_user_id,
                        starts_at=starts_at,
                        duration_days=days,
                        duration_km=km,
                        expires_at=starts_at + timedelta(days=days),
                        expiration_mileage=(
                            order.intake_mileage + km if order.intake_mileage is not None else None
                        ),
                    )

                for task in tasks:
                    self.db.add(
                        _workshop_warranty(task, WorkshopWarrantyCoverage.MANO_DE_OBRA, labor_days, labor_km)
                    )
                    if task.id in task_ids_with_parts:
                        self.db.add(
                            _workshop_warranty(task, WorkshopWarrantyCoverage.REPUESTO, parts_days, parts_km)
                        )

            await self.db.commit()
            return invoice
        except IntegrityError as exc:
            await self.db.rollback()
            raise ConflictError("La factura o la solicitud de cobro ya existe.") from exc
        except Exception:
            await self.db.rollback()
            raise

    # Cuentas por cobrar

    @staticmethod
    def _net_expected(invoice: ServiceOrderInvoice) -> float:
        """total_usd minus whatever a contribuyente especial withholds — a
        withheld amount is never collected as cash, so it must not count
        toward what's still owed."""
        return (
            float(invoice.total_usd)
            - float(invoice.iva_retention_amount or 0)
            - float(invoice.islr_retention_amount or 0)
        )

    def _receivable_to_read(self, invoice: ServiceOrderInvoice, order: ServiceOrder, client: Client) -> ReceivableRead:
        net_expected = self._net_expected(invoice)
        days_outstanding = (venezuela_today() - invoice.issued_at.date()).days
        return ReceivableRead(
            document_type="service_order_invoice",
            invoice_id=invoice.id,
            code=invoice.code,
            service_order_id=order.id,
            order_code=order.code,
            filial_id=order.filial_id,
            billed_client_id=client.id,
            billed_client_name=client.full_name,
            total_usd=float(invoice.total_usd),
            iva_retention_amount=float(invoice.iva_retention_amount or 0),
            islr_retention_amount=float(invoice.islr_retention_amount or 0),
            net_expected=net_expected,
            amount_paid_at_issuance=float(invoice.amount_paid_at_issuance),
            pending_amount=net_expected - float(invoice.amount_paid_at_issuance),
            issued_at=invoice.issued_at,
            days_outstanding=days_outstanding,
            aging_bucket=aging_bucket(days_outstanding),
        )

    async def get_invoice_by_id(self, invoice_id: uuid.UUID) -> ServiceOrderInvoice:
        invoice = await self.db.get(ServiceOrderInvoice, invoice_id)
        if invoice is None:
            raise InvoiceNotFoundError(str(invoice_id))
        return invoice

    async def list_receivables(self, filial_id: uuid.UUID) -> list[ReceivableRead]:
        result = await self.db.execute(
            select(ServiceOrderInvoice, ServiceOrder, Client)
            .join(ServiceOrder, ServiceOrder.id == ServiceOrderInvoice.service_order_id)
            .join(Client, Client.id == ServiceOrderInvoice.billed_client_id)
            .where(ServiceOrder.filial_id == filial_id, ServiceOrderInvoice.collected_at.is_(None))
            .order_by(ServiceOrderInvoice.issued_at)
        )
        return [self._receivable_to_read(invoice, order, client) for invoice, order, client in result.all()]

    async def collect_invoice(
        self, invoice_id: uuid.UUID, payload: CollectInvoicePaymentInput, collected_by_user_id: uuid.UUID | None
    ) -> ReceivableRead:
        invoice = await self.get_invoice_by_id(invoice_id)
        if invoice.collected_at is not None:
            raise InvoiceAlreadyCollectedError()

        account = await self.db.get(Account, payload.account_id)
        if account is None or account.currency != AccountCurrency.USD:
            raise ReceivableCurrencyMismatchError()

        order = await self.db.get(ServiceOrder, invoice.service_order_id)
        income = IncomeEntry(
            filial_id=order.filial_id,
            entry_date=billing_day(),
            source=IncomeSource.MANUAL,
            origin_reference=invoice.code,
            description=f"Cobro de cuenta por cobrar — {invoice.code}",
            amount=float(payload.net_collected_amount),
            currency=AccountCurrency.USD,
            account_id=account.id,
            registered_by_user_id=collected_by_user_id,
            source_type=MovementSourceType.SERVICE_ORDER,
            source_id=order.id,
        )
        self.db.add(income)
        await self.db.flush()

        invoice.withholding_amount = float(payload.withholding_amount)
        invoice.net_collected_amount = float(payload.net_collected_amount)
        invoice.collection_account_id = account.id
        invoice.collection_income_entry_id = income.id
        invoice.collected_by_user_id = collected_by_user_id
        invoice.collected_at = datetime.now(UTC)

        await self.db.commit()
        await self.db.refresh(invoice)

        client = await self.db.get(Client, invoice.billed_client_id)
        return self._receivable_to_read(invoice, order, client)

    async def get_holding_warranty_receivables(self, holding_id: uuid.UUID) -> HoldingWarrantyReceivablesReport:
        filiales = (
            (await self.db.execute(select(Filial).where(Filial.holding_id == holding_id)))
            .scalars()
            .all()
        )
        rows = []
        for filial in filiales:
            result = await self.db.execute(
                select(ServiceOrderInvoice)
                .join(ServiceOrder, ServiceOrder.id == ServiceOrderInvoice.service_order_id)
                .join(Client, Client.id == ServiceOrderInvoice.billed_client_id)
                .where(ServiceOrder.filial_id == filial.id, Client.is_holding_billing.is_(True))
            )
            invoices = result.scalars().all()
            total_invoiced = sum(float(i.total_usd) for i in invoices)
            total_pending = sum(
                self._net_expected(i) - float(i.amount_paid_at_issuance) for i in invoices if i.collected_at is None
            )
            total_collected = sum(float(i.net_collected_amount or 0) for i in invoices if i.collected_at is not None)
            total_withheld = sum(
                float(i.iva_retention_amount or 0) + float(i.islr_retention_amount or 0) + float(i.withholding_amount or 0)
                for i in invoices
            )
            rows.append(
                HoldingWarrantyFilialReceivables(
                    filial_id=filial.id,
                    filial_name=filial.name,
                    total_invoiced=total_invoiced,
                    total_pending=total_pending,
                    total_collected=total_collected,
                    total_withheld=total_withheld,
                )
            )
        return HoldingWarrantyReceivablesReport(holding_id=holding_id, filiales=rows)
