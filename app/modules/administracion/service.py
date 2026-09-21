import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.venezuela_time import aging_bucket, venezuela_today
from app.modules.administracion.enums import (
    AccountCurrency,
    ClaimResolution,
    ClaimStatus,
    ExpenseCategory,
    IncomeConcept,
    IncomeSource,
    MovementSourceType,
    PurchaseRequestStatus,
    WarrantySubmissionStatus,
)
from app.modules.administracion.exceptions import (
    AccountNotFoundError,
    AttachmentRequiredError,
    ClaimAccountRequiredError,
    ClaimAmountRequiredError,
    ClaimClientRequiredError,
    ClaimCurrencyMismatchError,
    ClaimMustBeResolvedThroughResolveEndpointError,
    ClaimNotFoundError,
    ClaimNotRejectedError,
    ClosedPeriodEntryDateError,
    EntryAlreadyReversedError,
    EntryNotFoundError,
    ExchangeRateRequiredError,
    FutureEntryDateError,
    InvalidPurchaseStatusTransitionError,
    PurchaseRequestNotFoundError,
    QuoteRequiredError,
    SupplierNotFoundError,
    WarehouseRequiredError,
    WarrantySubmissionAlreadyExistsError,
    WarrantySubmissionEmptyError,
    WarrantySubmissionNotEditableError,
    WarrantySubmissionNotFoundError,
)
from app.modules.administracion.models import (
    Account,
    ExpenseEntry,
    IncomeEntry,
    PurchaseRequest,
    PurchaseRequestLine,
    Supplier,
    SupplierPaymentAccount,
    SupplierClaim,
    WarrantySubmission,
)
from app.modules.administracion.schemas import (
    AccountCreate,
    AccountUpdate,
    ExpenseEntryCreate,
    FinanceDashboard,
    IncomeEntryCreate,
    MonthTrend,
    ProfitabilityAdjustmentRow,
    ProfitabilityDepartmentRow,
    ProfitabilityLineItem,
    ProfitabilityReport,
    PurchaseRequestCreate,
    PurchaseRequestLineRead,
    PurchaseRequestRead,
    SupplierClaimCreate,
    SupplierClaimResolveInput,
    SupplierClaimUpdate,
    SupplierCreate,
    SupplierDetailRead,
    SupplierRead,
    SupplierUpdate,
    WarrantySubmissionClaimRead,
    WarrantySubmissionCreate,
    WarrantySubmissionPayInput,
    WarrantySubmissionRead,
)
from app.modules.warehouse.enums import MovementType
from app.modules.warehouse.models import StockMovement
from app.modules.warehouse.schemas import LotLineInput
from app.modules.warehouse.service import AlmacenService
from app.modules.concesionario.enums import VehicleCondition
from app.modules.concesionario.models import DealershipVehicle, VehicleSale
from app.modules.filiales.models import Filial
from app.modules.parts.enums import PartSaleStatus
from app.modules.parts.models import Part, PartSale, PartSaleLine
from app.modules.post_ventas.models import Tempario
from app.modules.post_ventas.service import PostVentasService
from app.modules.clients.models import Client
from app.modules.service_orders.enums import TransferStatus, WarrantyClaimType
from app.modules.service_orders.models import (
    ServiceOrder,
    ServiceOrderInvoice,
    ServiceOrderTransfer,
    ServiceOrderTransferLine,
    ServiceOrderTransferLotAllocation,
    WarrantyClaim,
)

# Spanish month abbreviations for the finance trend chart — not read from
# calendar.month_abbr, which is locale-dependent and defaults to English.
MONTH_ABBR_ES: dict[int, str] = {
    1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic",
}

PURCHASE_TRANSITIONS: dict[PurchaseRequestStatus, set[PurchaseRequestStatus]] = {
    PurchaseRequestStatus.ENVIADA: {PurchaseRequestStatus.COTIZADA, PurchaseRequestStatus.CANCELADA},
    PurchaseRequestStatus.COTIZADA: {PurchaseRequestStatus.PAGADA, PurchaseRequestStatus.CANCELADA},
    PurchaseRequestStatus.PAGADA: {PurchaseRequestStatus.RECIBIDA, PurchaseRequestStatus.CANCELADA},
    PurchaseRequestStatus.RECIBIDA: {PurchaseRequestStatus.CONCILIADA},
    PurchaseRequestStatus.CONCILIADA: set(),
    PurchaseRequestStatus.CANCELADA: set(),
}


def _request_to_read(request: PurchaseRequest) -> PurchaseRequestRead:
    total = None
    if request.lines and all(line.unit_cost is not None for line in request.lines):
        total = sum(line.quantity * float(line.unit_cost) for line in request.lines)
    return PurchaseRequestRead(
        id=request.id,
        filial_id=request.filial_id,
        code=request.code,
        supplier_id=request.supplier_id,
        status=request.status,
        lines=[
            PurchaseRequestLineRead(
                id=line.id,
                part_id=line.part_id,
                quantity=line.quantity,
                unit_cost=float(line.unit_cost) if line.unit_cost is not None else None,
                subtotal=line.quantity * float(line.unit_cost) if line.unit_cost is not None else None,
            )
            for line in request.lines
        ],
        total_quoted=total,
        created_at=request.created_at,
        updated_at=request.updated_at,
    )


def _profitability_department_row(key: str, label: str, net_sales: float, direct_cost: float) -> ProfitabilityDepartmentRow:
    gross_profit = net_sales - direct_cost
    margin = gross_profit / net_sales if net_sales else 0.0
    return ProfitabilityDepartmentRow(
        key=key, label=label, net_sales=net_sales, direct_cost=direct_cost,
        gross_profit=gross_profit, margin=margin,
    )


class AdministracionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # Suppliers

    async def list_suppliers(self, filial_id: uuid.UUID, search: str | None = None) -> list[Supplier]:
        result = await self.db.execute(
            select(Supplier).where(Supplier.filial_id == filial_id).order_by(Supplier.business_name)
        )
        suppliers = list(result.scalars().all())
        if search:
            term = search.lower()
            suppliers = [s for s in suppliers if term in s.business_name.lower() or term in s.rif.lower()]
        return suppliers

    async def get_supplier(self, supplier_id: uuid.UUID) -> Supplier:
        supplier = await self.db.get(Supplier, supplier_id)
        if supplier is None:
            raise SupplierNotFoundError(str(supplier_id))
        return supplier

    async def get_supplier_detail(self, supplier_id: uuid.UUID) -> SupplierDetailRead:
        result = await self.db.execute(
            select(Supplier)
            .options(selectinload(Supplier.payment_accounts))
            .where(Supplier.id == supplier_id)
        )
        supplier = result.scalar_one_or_none()
        if supplier is None:
            raise SupplierNotFoundError(str(supplier_id))
        purchases_result = await self.db.execute(
            select(PurchaseRequest)
            .options(selectinload(PurchaseRequest.lines))
            .where(PurchaseRequest.supplier_id == supplier_id)
            .order_by(PurchaseRequest.created_at.desc())
        )
        data = SupplierRead.model_validate(supplier).model_dump()
        data["payment_accounts"] = supplier.payment_accounts
        data["purchase_history"] = [_request_to_read(item) for item in purchases_result.scalars().all()]
        return SupplierDetailRead.model_validate(data)

    async def create_supplier(self, payload: SupplierCreate) -> Supplier:
        data = payload.model_dump(exclude={"payment_accounts"})
        supplier = Supplier(**data)
        supplier.payment_accounts = [
            SupplierPaymentAccount(**account.model_dump()) for account in payload.payment_accounts
        ]
        self.db.add(supplier)
        await self.db.commit()
        await self.db.refresh(supplier)
        return supplier

    async def update_supplier(self, supplier_id: uuid.UUID, payload: SupplierUpdate) -> Supplier:
        supplier = await self.get_supplier(supplier_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(supplier, field, value)
        await self.db.commit()
        await self.db.refresh(supplier)
        return supplier

    # Purchase requests

    async def _next_request_sequence(self, filial_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.max(PurchaseRequest.sequence_number)).where(PurchaseRequest.filial_id == filial_id)
        )
        current_max = result.scalar()
        return (current_max or 1000) + 1

    async def list_requests(self, filial_id: uuid.UUID, search: str | None = None) -> list[PurchaseRequestRead]:
        result = await self.db.execute(
            select(PurchaseRequest)
            .options(selectinload(PurchaseRequest.lines))
            .where(PurchaseRequest.filial_id == filial_id)
            .order_by(PurchaseRequest.created_at.desc())
        )
        requests = list(result.scalars().all())
        reads = [_request_to_read(r) for r in requests]
        if search:
            term = search.lower()
            reads = [r for r in reads if term in r.code.lower()]
        return reads

    async def _get_request_model(self, request_id: uuid.UUID) -> PurchaseRequest:
        result = await self.db.execute(
            select(PurchaseRequest)
            .options(selectinload(PurchaseRequest.lines))
            .where(PurchaseRequest.id == request_id)
        )
        request = result.scalar_one_or_none()
        if request is None:
            raise PurchaseRequestNotFoundError(str(request_id))
        return request

    async def get_request(self, request_id: uuid.UUID) -> PurchaseRequestRead:
        return _request_to_read(await self._get_request_model(request_id))

    async def create_request(self, payload: PurchaseRequestCreate) -> PurchaseRequestRead:
        sequence_number = await self._next_request_sequence(payload.filial_id)
        request = PurchaseRequest(
            filial_id=payload.filial_id,
            sequence_number=sequence_number,
            supplier_id=payload.supplier_id,
        )
        self.db.add(request)
        await self.db.flush()

        for line in payload.lines:
            self.db.add(
                PurchaseRequestLine(purchase_request_id=request.id, part_id=line.part_id, quantity=line.quantity)
            )

        await self.db.commit()
        return await self.get_request(request.id)

    async def update_request_status(
        self,
        request_id: uuid.UUID,
        new_status: PurchaseRequestStatus,
        quotes: list | None,
        warehouse_id: uuid.UUID | None,
        location: str | None = None,
        responsible_user_id: uuid.UUID | None = None,
    ) -> PurchaseRequestRead:
        request = await self._get_request_model(request_id)
        if new_status != request.status:
            if new_status not in PURCHASE_TRANSITIONS.get(request.status, set()):
                raise InvalidPurchaseStatusTransitionError(request.status.value, new_status.value)

            if new_status == PurchaseRequestStatus.COTIZADA:
                quote_map = {q.line_id: q.unit_cost for q in (quotes or [])}
                if len(quote_map) < len(request.lines):
                    raise QuoteRequiredError()
                for line in request.lines:
                    if line.id not in quote_map:
                        raise QuoteRequiredError()
                    line.unit_cost = quote_map[line.id]

            if new_status == PurchaseRequestStatus.RECIBIDA:
                if warehouse_id is None:
                    raise WarehouseRequiredError()
                almacen = AlmacenService(self.db)
                for line in request.lines:
                    if line.unit_cost is None:
                        raise QuoteRequiredError()
                    await almacen._create_single_lot(
                        request.filial_id,
                        warehouse_id,
                        LotLineInput(
                            part_id=line.part_id,
                            quantity=line.quantity,
                            unit_cost=float(line.unit_cost),
                            purchase_request_id=request.id,
                            location=location,
                        ),
                        note=f"Compra {request.code}",
                        responsible_user_id=responsible_user_id,
                    )
                request.warehouse_id = warehouse_id

            request.status = new_status

        await self.db.commit()
        return await self.get_request(request_id)

    # Supplier claims

    async def list_claims(self, filial_id: uuid.UUID, search: str | None = None) -> list[SupplierClaim]:
        result = await self.db.execute(
            select(SupplierClaim)
            .where(SupplierClaim.filial_id == filial_id)
            .order_by(SupplierClaim.created_at.desc())
        )
        claims = list(result.scalars().all())
        await self._attach_client_name(claims)
        return claims

    async def get_claim(self, claim_id: uuid.UUID) -> SupplierClaim:
        claim = await self.db.get(SupplierClaim, claim_id)
        if claim is None:
            raise ClaimNotFoundError(str(claim_id))
        return claim

    async def _attach_client_name(self, claims: list[SupplierClaim]) -> None:
        client_ids = {claim.client_id for claim in claims if claim.client_id is not None}
        names: dict[uuid.UUID, str] = {}
        if client_ids:
            result = await self.db.execute(select(Client).where(Client.id.in_(client_ids)))
            names = {client.id: client.full_name for client in result.scalars().all()}
        for claim in claims:
            claim.client_name = names.get(claim.client_id) if claim.client_id else None

    async def create_claim(self, payload: SupplierClaimCreate) -> SupplierClaim:
        claim = SupplierClaim(**payload.model_dump())
        self.db.add(claim)
        await self.db.commit()
        await self.db.refresh(claim)
        await self._attach_client_name([claim])
        return claim

    async def update_claim(self, claim_id: uuid.UUID, payload: SupplierClaimUpdate) -> SupplierClaim:
        claim = await self.get_claim(claim_id)
        if payload.status == ClaimStatus.RESUELTO:
            raise ClaimMustBeResolvedThroughResolveEndpointError()
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(claim, field, value)
        await self.db.commit()
        await self.db.refresh(claim)
        await self._attach_client_name([claim])
        return claim

    async def resolve_claim(
        self, claim_id: uuid.UUID, payload: SupplierClaimResolveInput, resolved_by_user_id: uuid.UUID | None
    ) -> SupplierClaim:
        claim = await self.get_claim(claim_id)
        if claim.status != ClaimStatus.RECHAZADO:
            raise ClaimNotRejectedError()
        if claim.claimed_amount is None or claim.currency is None:
            raise ClaimAmountRequiredError()

        if payload.client_id is not None:
            claim.client_id = payload.client_id

        if payload.resolution == ClaimResolution.CARGO_CLIENTE:
            if claim.client_id is None:
                raise ClaimClientRequiredError()
        else:
            if payload.account_id is None:
                raise ClaimAccountRequiredError()
            account = await self.get_account(payload.account_id)
            if account.currency != claim.currency:
                raise ClaimCurrencyMismatchError()

            supplier = await self.db.get(Supplier, claim.supplier_id)
            expense = ExpenseEntry(
                filial_id=claim.filial_id,
                entry_date=venezuela_today(),
                category=ExpenseCategory.GARANTIA_RECHAZADA,
                beneficiary=supplier.business_name if supplier else "Importador",
                description=f"Factura rechazada por el importador — reclamo {claim.id}",
                amount=claim.claimed_amount,
                currency=claim.currency,
                account_id=account.id,
                registered_by_user_id=resolved_by_user_id,
                source_type=MovementSourceType.SUPPLIER_CLAIM,
                source_id=claim.id,
            )
            self.db.add(expense)
            await self.db.flush()
            claim.expense_entry_id = expense.id

        claim.resolution = payload.resolution
        claim.resolution_note = payload.note
        claim.resolved_by_user_id = resolved_by_user_id
        claim.resolved_at = datetime.now(timezone.utc)
        claim.status = ClaimStatus.RESUELTO

        await self.db.commit()
        await self.db.refresh(claim)
        await self._attach_client_name([claim])
        return claim

    # Warranty submissions (monthly presentación to the holding)

    @staticmethod
    def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12 else datetime(year, month + 1, 1, tzinfo=timezone.utc)
        return start, end

    async def _next_warranty_submission_sequence(self, filial_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.max(WarrantySubmission.sequence_number)).where(WarrantySubmission.filial_id == filial_id)
        )
        current_max = result.scalar()
        return (current_max or 0) + 1

    async def _unclaimed_costo_taller_claims(
        self, filial_id: uuid.UUID, period_year: int, period_month: int, currency: AccountCurrency
    ) -> list[SupplierClaim]:
        start, end = self._month_bounds(period_year, period_month)
        result = await self.db.execute(
            select(SupplierClaim).where(
                SupplierClaim.filial_id == filial_id,
                SupplierClaim.resolution == ClaimResolution.COSTO_TALLER,
                SupplierClaim.currency == currency,
                SupplierClaim.warranty_submission_id.is_(None),
                SupplierClaim.resolved_at >= start,
                SupplierClaim.resolved_at < end,
            )
        )
        return list(result.scalars().all())

    def _submission_to_read(self, submission: WarrantySubmission) -> WarrantySubmissionRead:
        total = sum(float(c.claimed_amount or 0) for c in submission.claims)
        return WarrantySubmissionRead(
            id=submission.id,
            filial_id=submission.filial_id,
            code=submission.code,
            period_year=submission.period_year,
            period_month=submission.period_month,
            currency=submission.currency,
            status=submission.status,
            claims=[WarrantySubmissionClaimRead.model_validate(c) for c in submission.claims],
            total_claimed_amount=total,
            submitted_at=submission.submitted_at,
            submitted_by_user_id=submission.submitted_by_user_id,
            withholding_amount=float(submission.withholding_amount) if submission.withholding_amount is not None else None,
            net_amount_received=float(submission.net_amount_received) if submission.net_amount_received is not None else None,
            account_id=submission.account_id,
            income_entry_id=submission.income_entry_id,
            paid_at=submission.paid_at,
            paid_by_user_id=submission.paid_by_user_id,
            created_at=submission.created_at,
            updated_at=submission.updated_at,
        )

    async def _get_submission_model(self, submission_id: uuid.UUID) -> WarrantySubmission:
        # populate_existing forces a refresh of `claims` even when this
        # WarrantySubmission is already in the identity map from an earlier
        # query in the same session — otherwise claims just re-attached by
        # create/refresh wouldn't show up (see PostVentasService._get_plan_model
        # for the same fix applied to MaintenancePlan.entries).
        result = await self.db.execute(
            select(WarrantySubmission)
            .options(selectinload(WarrantySubmission.claims))
            .where(WarrantySubmission.id == submission_id)
            .execution_options(populate_existing=True)
        )
        submission = result.scalar_one_or_none()
        if submission is None:
            raise WarrantySubmissionNotFoundError(str(submission_id))
        return submission

    async def list_warranty_submissions(self, filial_id: uuid.UUID) -> list[WarrantySubmissionRead]:
        result = await self.db.execute(
            select(WarrantySubmission)
            .options(selectinload(WarrantySubmission.claims))
            .where(WarrantySubmission.filial_id == filial_id)
            .order_by(WarrantySubmission.period_year.desc(), WarrantySubmission.period_month.desc())
        )
        submissions = list(result.scalars().unique().all())
        return [self._submission_to_read(s) for s in submissions]

    async def get_warranty_submission(self, submission_id: uuid.UUID) -> WarrantySubmissionRead:
        return self._submission_to_read(await self._get_submission_model(submission_id))

    async def create_warranty_submission(self, payload: WarrantySubmissionCreate) -> WarrantySubmissionRead:
        existing = await self.db.execute(
            select(WarrantySubmission).where(
                WarrantySubmission.filial_id == payload.filial_id,
                WarrantySubmission.period_year == payload.period_year,
                WarrantySubmission.period_month == payload.period_month,
                WarrantySubmission.currency == payload.currency,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise WarrantySubmissionAlreadyExistsError()

        sequence_number = await self._next_warranty_submission_sequence(payload.filial_id)
        submission = WarrantySubmission(
            filial_id=payload.filial_id,
            sequence_number=sequence_number,
            period_year=payload.period_year,
            period_month=payload.period_month,
            currency=payload.currency,
        )
        self.db.add(submission)
        await self.db.flush()

        claims = await self._unclaimed_costo_taller_claims(
            payload.filial_id, payload.period_year, payload.period_month, payload.currency
        )
        for claim in claims:
            claim.warranty_submission_id = submission.id

        await self.db.commit()
        return await self.get_warranty_submission(submission.id)

    async def refresh_warranty_submission(self, submission_id: uuid.UUID) -> WarrantySubmissionRead:
        submission = await self._get_submission_model(submission_id)
        if submission.status != WarrantySubmissionStatus.BORRADOR:
            raise WarrantySubmissionNotEditableError()

        claims = await self._unclaimed_costo_taller_claims(
            submission.filial_id, submission.period_year, submission.period_month, submission.currency
        )
        for claim in claims:
            claim.warranty_submission_id = submission.id

        await self.db.commit()
        return await self.get_warranty_submission(submission_id)

    async def submit_warranty_submission(
        self, submission_id: uuid.UUID, submitted_by_user_id: uuid.UUID | None
    ) -> WarrantySubmissionRead:
        submission = await self._get_submission_model(submission_id)
        if submission.status != WarrantySubmissionStatus.BORRADOR:
            raise WarrantySubmissionNotEditableError()
        if not submission.claims:
            raise WarrantySubmissionEmptyError()

        submission.status = WarrantySubmissionStatus.PRESENTADA
        submission.submitted_at = datetime.now(timezone.utc)
        submission.submitted_by_user_id = submitted_by_user_id

        await self.db.commit()
        return await self.get_warranty_submission(submission_id)

    async def mark_warranty_submission_paid(
        self, submission_id: uuid.UUID, payload: WarrantySubmissionPayInput, paid_by_user_id: uuid.UUID | None
    ) -> WarrantySubmissionRead:
        submission = await self._get_submission_model(submission_id)
        if submission.status != WarrantySubmissionStatus.PRESENTADA:
            raise WarrantySubmissionNotEditableError()

        account = await self.get_account(payload.account_id)
        if account.currency != submission.currency:
            raise ClaimCurrencyMismatchError()

        income = IncomeEntry(
            filial_id=submission.filial_id,
            entry_date=venezuela_today(),
            source=IncomeSource.MANUAL,
            origin_reference=submission.code,
            description=f"Reembolso de garantías del holding — {submission.code}",
            amount=payload.net_amount_received,
            currency=submission.currency,
            account_id=account.id,
            registered_by_user_id=paid_by_user_id,
            source_type=MovementSourceType.WARRANTY_SUBMISSION,
            source_id=submission.id,
        )
        self.db.add(income)
        await self.db.flush()

        submission.withholding_amount = payload.withholding_amount
        submission.net_amount_received = payload.net_amount_received
        submission.account_id = account.id
        submission.income_entry_id = income.id
        submission.paid_at = datetime.now(timezone.utc)
        submission.paid_by_user_id = paid_by_user_id
        submission.status = WarrantySubmissionStatus.PAGADA

        await self.db.commit()
        return await self.get_warranty_submission(submission_id)

    async def delete_warranty_submission(self, submission_id: uuid.UUID) -> None:
        submission = await self._get_submission_model(submission_id)
        if submission.status != WarrantySubmissionStatus.BORRADOR:
            raise WarrantySubmissionNotEditableError()
        for claim in submission.claims:
            claim.warranty_submission_id = None
        await self.db.delete(submission)
        await self.db.commit()

    async def export_warranty_submission_csv(self, submission_id: uuid.UUID) -> tuple[str, str]:
        submission = await self._get_submission_model(submission_id)
        supplier_ids = {c.supplier_id for c in submission.claims}
        part_ids = {c.part_id for c in submission.claims}
        suppliers: dict[uuid.UUID, str] = {}
        parts: dict[uuid.UUID, str] = {}
        if supplier_ids:
            result = await self.db.execute(select(Supplier).where(Supplier.id.in_(supplier_ids)))
            suppliers = {s.id: s.business_name for s in result.scalars().all()}
        if part_ids:
            from app.modules.parts.models import Part

            result = await self.db.execute(select(Part).where(Part.id.in_(part_ids)))
            parts = {p.id: p.name for p in result.scalars().all()}

        import csv
        import io

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Fecha resolución", "Proveedor", "Repuesto", "Cantidad", "Monto", "Moneda", "Nota"])
        for claim in submission.claims:
            writer.writerow([
                claim.resolved_at.date().isoformat() if claim.resolved_at else "",
                suppliers.get(claim.supplier_id, ""),
                parts.get(claim.part_id, ""),
                claim.quantity,
                float(claim.claimed_amount or 0),
                claim.currency.value if claim.currency else "",
                claim.resolution_note or "",
            ])
        return buffer.getvalue(), f"{submission.code}.csv"

    # Accounts

    async def _get_bcv_rate(self, filial_id: uuid.UUID) -> float:
        settings = await PostVentasService(self.db).get_labor_settings(filial_id)
        return float(settings.bcv_rate) if settings.bcv_rate else 0.0

    async def _account_balance(self, account: Account, bcv_rate: float) -> tuple[float, float]:
        income_result = await self.db.execute(
            select(func.coalesce(func.sum(IncomeEntry.amount), 0)).where(
                IncomeEntry.account_id == account.id, IncomeEntry.currency == account.currency
            )
        )
        expense_result = await self.db.execute(
            select(func.coalesce(func.sum(ExpenseEntry.amount), 0)).where(
                ExpenseEntry.account_id == account.id, ExpenseEntry.currency == account.currency
            )
        )
        balance = (
            float(account.opening_balance)
            + float(income_result.scalar() or 0)
            - float(expense_result.scalar() or 0)
        )
        balance_usd = balance if account.currency == AccountCurrency.USD else (balance / bcv_rate if bcv_rate else 0.0)
        return balance, balance_usd

    async def _account_row(self, account: Account, bcv_rate: float) -> dict:
        balance, balance_usd = await self._account_balance(account, bcv_rate)
        return {
            "id": account.id,
            "filial_id": account.filial_id,
            "name": account.name,
            "bank": account.bank,
            "currency": account.currency,
            "account_type": account.account_type,
            "opening_balance": float(account.opening_balance),
            "is_active": account.is_active,
            "balance": balance,
            "balance_usd": balance_usd,
            "created_at": account.created_at,
        }

    async def list_accounts(self, filial_id: uuid.UUID) -> list[dict]:
        result = await self.db.execute(
            select(Account).where(Account.filial_id == filial_id).order_by(Account.created_at)
        )
        accounts = list(result.scalars().all())
        bcv_rate = await self._get_bcv_rate(filial_id)
        return [await self._account_row(account, bcv_rate) for account in accounts]

    async def get_account_detail(self, account_id: uuid.UUID) -> dict:
        account = await self.get_account(account_id)
        bcv_rate = await self._get_bcv_rate(account.filial_id)
        return await self._account_row(account, bcv_rate)

    async def get_account_movements(self, account_id: uuid.UUID, limit: int = 50) -> list[dict]:
        """The account's own income/expense entries, most recent first —
        merged into one feed so the account-detail screen can show a single
        chronological list instead of two separate tables."""
        income_result = await self.db.execute(
            select(IncomeEntry)
            .where(IncomeEntry.account_id == account_id)
            .order_by(IncomeEntry.entry_date.desc(), IncomeEntry.created_at.desc())
            .limit(limit)
        )
        expense_result = await self.db.execute(
            select(ExpenseEntry)
            .where(ExpenseEntry.account_id == account_id)
            .order_by(ExpenseEntry.entry_date.desc(), ExpenseEntry.created_at.desc())
            .limit(limit)
        )
        movements = [
            {
                "id": e.id,
                "movement_type": "ingreso",
                "entry_date": e.entry_date,
                "description": e.description,
                "amount": float(e.amount),
                "currency": e.currency,
                "concept": e.concept,
                "category": None,
                "counterparty_type": e.counterparty_type,
                "counterparty_client_id": e.counterparty_client_id,
                "counterparty_supplier_id": e.counterparty_supplier_id,
                "counterparty_name": e.counterparty_name,
                "reference": e.reference,
                "attachment_url": e.attachment_url,
                "reverses_entry_id": e.reverses_entry_id,
                "source_type": e.source_type,
                "source_id": e.source_id,
                "created_at": e.created_at,
            }
            for e in income_result.scalars().all()
        ] + [
            {
                "id": e.id,
                "movement_type": "egreso",
                "entry_date": e.entry_date,
                "description": e.description,
                "amount": float(e.amount),
                "currency": e.currency,
                "concept": None,
                "category": e.category,
                "counterparty_type": e.counterparty_type,
                "counterparty_client_id": e.counterparty_client_id,
                "counterparty_supplier_id": e.counterparty_supplier_id,
                "counterparty_name": e.counterparty_name,
                "reference": e.reference,
                "attachment_url": e.attachment_url,
                "reverses_entry_id": e.reverses_entry_id,
                "source_type": e.source_type,
                "source_id": e.source_id,
                "created_at": e.created_at,
            }
            for e in expense_result.scalars().all()
        ]
        movements.sort(key=lambda m: (m["entry_date"], m["created_at"]), reverse=True)
        return movements[:limit]

    async def get_account(self, account_id: uuid.UUID) -> Account:
        account = await self.db.get(Account, account_id)
        if account is None:
            raise AccountNotFoundError(str(account_id))
        return account

    async def record_automatic_income(
        self,
        filial_id: uuid.UUID,
        description: str,
        amount: float,
        origin_reference: str,
        source_type: MovementSourceType | None = None,
        source_id: uuid.UUID | None = None,
    ) -> IncomeEntry | None:
        """Called by other modules (Servicios, Repuestos, Concesionario) when they
        close something billable. Posts to the filial's first active USD account.
        If there's no USD account yet, it silently skips instead of blocking
        whatever operation triggered it — the person can still register the
        income manually from Ingresos."""
        if amount <= 0:
            return None

        result = await self.db.execute(
            select(Account)
            .where(
                Account.filial_id == filial_id,
                Account.currency == AccountCurrency.USD,
                Account.is_active.is_(True),
            )
            .order_by(Account.created_at)
        )
        account = result.scalars().first()
        if account is None:
            return None

        entry = IncomeEntry(
            filial_id=filial_id,
            entry_date=venezuela_today(),
            source=IncomeSource.AUTOMATICO,
            origin_reference=origin_reference,
            description=description,
            amount=amount,
            currency=AccountCurrency.USD,
            account_id=account.id,
            source_type=source_type,
            source_id=source_id,
        )
        self.db.add(entry)
        await self.db.commit()
        return entry

    async def create_account(self, payload: AccountCreate) -> Account:
        account = Account(**payload.model_dump())
        self.db.add(account)
        await self.db.commit()
        await self.db.refresh(account)
        return account

    async def update_account(self, account_id: uuid.UUID, payload: AccountUpdate) -> Account:
        account = await self.get_account(account_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(account, field, value)
        await self.db.commit()
        await self.db.refresh(account)
        return account

    # Income / Expense

    async def list_income(self, filial_id: uuid.UUID, search: str | None = None) -> list[IncomeEntry]:
        result = await self.db.execute(
            select(IncomeEntry).where(IncomeEntry.filial_id == filial_id).order_by(IncomeEntry.entry_date.desc())
        )
        entries = list(result.scalars().all())
        if search:
            term = search.lower()
            entries = [e for e in entries if term in e.description.lower() or (e.origin_reference and term in e.origin_reference.lower())]
        return entries

    def _assert_open_period(self, entry_date: date) -> None:
        """"Open period" = the current calendar month, no future dates — no
        Período entity, no close/reopen screen; a month is "closed" purely
        by no longer being the current one."""
        today = venezuela_today()
        if entry_date > today:
            raise FutureEntryDateError()
        if (entry_date.year, entry_date.month) != (today.year, today.month):
            raise ClosedPeriodEntryDateError()

    async def _freeze_rate(
        self, currency: AccountCurrency, entry_date: date, amount: float
    ) -> tuple[float | None, float, float | None]:
        """Returns (exchange_rate, amount_usd, amount_bs) using the BCV rate
        on/before entry_date — frozen at save time, never re-derived from a
        rate that later moves. Required (blocking) only when the movement's
        own currency isn't USD; best-effort otherwise."""
        from app.modules.exchange_rates.service import ExchangeRateService

        rate_row = await ExchangeRateService(self.db).as_of("USD", entry_date)
        # rate_ves is a Numeric column — SQLAlchemy hands back a real
        # decimal.Decimal for it on a genuinely fresh row load (every
        # request in production), which float can't be divided/multiplied
        # against directly; must cast before doing arithmetic with `amount`.
        rate = float(rate_row.rate_ves) if rate_row else None
        if currency == AccountCurrency.USD:
            amount_bs = amount * rate if rate else None
            return rate, amount, amount_bs
        if rate is None:
            raise ExchangeRateRequiredError()
        return rate, amount / rate, amount

    async def _require_attachment_if_needed(
        self, filial_id: uuid.UUID, amount_usd: float, attachment
    ) -> str | None:
        settings = await PostVentasService(self.db).get_labor_settings(filial_id)
        threshold = float(settings.manual_movement_attachment_threshold_usd)
        if attachment is None or not getattr(attachment, "filename", None):
            if amount_usd >= threshold:
                raise AttachmentRequiredError(threshold)
            return None

        from pathlib import Path

        from app.core.config import get_settings
        from app.core.storage import save_upload_attachment

        settings_app = get_settings()
        return await save_upload_attachment(
            attachment,
            directory=Path(settings_app.uploads_dir),
            subdir="manual-movements",
            url_prefix=f"{settings_app.api_v1_prefix}/uploads",
            max_mb=settings_app.max_upload_mb,
        )

    async def create_income(self, payload: IncomeEntryCreate, attachment, responsible_user_id: uuid.UUID | None) -> IncomeEntry:
        self._assert_open_period(payload.entry_date)
        await self.get_account(payload.account_id)
        exchange_rate, amount_usd, amount_bs = await self._freeze_rate(
            payload.currency, payload.entry_date, payload.amount
        )
        attachment_url = await self._require_attachment_if_needed(payload.filial_id, amount_usd, attachment)
        entry = IncomeEntry(
            **payload.model_dump(),
            source=IncomeSource.MANUAL,
            exchange_rate=exchange_rate,
            amount_usd=amount_usd,
            amount_bs=amount_bs,
            attachment_url=attachment_url,
            registered_by_user_id=responsible_user_id,
        )
        self.db.add(entry)
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    async def reverse_income(self, entry_id: uuid.UUID, user_id: uuid.UUID | None) -> IncomeEntry:
        original = await self.db.get(IncomeEntry, entry_id)
        if original is None:
            raise EntryNotFoundError(str(entry_id))
        existing = await self.db.execute(
            select(IncomeEntry).where(IncomeEntry.reverses_entry_id == entry_id)
        )
        if existing.scalar_one_or_none() is not None:
            raise EntryAlreadyReversedError()

        reversal = IncomeEntry(
            filial_id=original.filial_id,
            entry_date=venezuela_today(),
            source=IncomeSource.MANUAL,
            concept=original.concept,
            description=f"Reverso de: {original.description}",
            amount=-original.amount,
            currency=original.currency,
            account_id=original.account_id,
            counterparty_type=original.counterparty_type,
            counterparty_client_id=original.counterparty_client_id,
            counterparty_supplier_id=original.counterparty_supplier_id,
            counterparty_name=original.counterparty_name,
            reference=original.reference,
            exchange_rate=original.exchange_rate,
            amount_usd=-original.amount_usd if original.amount_usd is not None else None,
            amount_bs=-original.amount_bs if original.amount_bs is not None else None,
            reverses_entry_id=original.id,
            registered_by_user_id=user_id,
        )
        self.db.add(reversal)
        await self.db.commit()
        await self.db.refresh(reversal)
        return reversal

    async def list_expenses(self, filial_id: uuid.UUID, search: str | None = None) -> list[ExpenseEntry]:
        result = await self.db.execute(
            select(ExpenseEntry).where(ExpenseEntry.filial_id == filial_id).order_by(ExpenseEntry.entry_date.desc())
        )
        entries = list(result.scalars().all())
        if search:
            term = search.lower()
            entries = [
                e
                for e in entries
                if term in e.description.lower() or term in e.beneficiary.lower() or term in e.category.value
            ]
        return entries

    async def create_expense(
        self, payload: ExpenseEntryCreate, attachment, responsible_user_id: uuid.UUID | None
    ) -> ExpenseEntry:
        self._assert_open_period(payload.entry_date)
        await self.get_account(payload.account_id)
        exchange_rate, amount_usd, amount_bs = await self._freeze_rate(
            payload.currency, payload.entry_date, payload.amount
        )
        attachment_url = await self._require_attachment_if_needed(payload.filial_id, amount_usd, attachment)
        entry = ExpenseEntry(
            **payload.model_dump(),
            exchange_rate=exchange_rate,
            amount_usd=amount_usd,
            amount_bs=amount_bs,
            attachment_url=attachment_url,
            registered_by_user_id=responsible_user_id,
        )
        self.db.add(entry)
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    async def reverse_expense(self, entry_id: uuid.UUID, user_id: uuid.UUID | None) -> ExpenseEntry:
        original = await self.db.get(ExpenseEntry, entry_id)
        if original is None:
            raise EntryNotFoundError(str(entry_id))
        existing = await self.db.execute(
            select(ExpenseEntry).where(ExpenseEntry.reverses_entry_id == entry_id)
        )
        if existing.scalar_one_or_none() is not None:
            raise EntryAlreadyReversedError()

        reversal = ExpenseEntry(
            filial_id=original.filial_id,
            entry_date=venezuela_today(),
            category=original.category,
            beneficiary=original.beneficiary,
            description=f"Reverso de: {original.description}",
            amount=-original.amount,
            currency=original.currency,
            account_id=original.account_id,
            counterparty_type=original.counterparty_type,
            counterparty_client_id=original.counterparty_client_id,
            counterparty_supplier_id=original.counterparty_supplier_id,
            counterparty_name=original.counterparty_name,
            reference=original.reference,
            exchange_rate=original.exchange_rate,
            amount_usd=-original.amount_usd if original.amount_usd is not None else None,
            amount_bs=-original.amount_bs if original.amount_bs is not None else None,
            reverses_entry_id=original.id,
            registered_by_user_id=user_id,
        )
        self.db.add(reversal)
        await self.db.commit()
        await self.db.refresh(reversal)
        return reversal

    # Reports

    def _usd_equivalent(self, amount: float, currency: AccountCurrency, bcv_rate: float) -> float:
        if currency == AccountCurrency.USD:
            return amount
        return amount / bcv_rate if bcv_rate else 0.0

    async def get_dashboard(self, filial_id: uuid.UUID) -> FinanceDashboard:
        settings = await PostVentasService(self.db).get_labor_settings(filial_id)
        bcv_rate = float(settings.bcv_rate) if settings.bcv_rate else 0.0
        today = venezuela_today()

        income_result = await self.db.execute(select(IncomeEntry).where(IncomeEntry.filial_id == filial_id))
        incomes = list(income_result.scalars().all())
        expense_result = await self.db.execute(select(ExpenseEntry).where(ExpenseEntry.filial_id == filial_id))
        expenses = list(expense_result.scalars().all())

        def month_key(d: date) -> tuple[int, int]:
            return (d.year, d.month)

        income_month = sum(
            self._usd_equivalent(float(e.amount), e.currency, bcv_rate)
            for e in incomes
            if month_key(e.entry_date) == month_key(today)
        )
        expense_month = sum(
            self._usd_equivalent(float(e.amount), e.currency, bcv_rate)
            for e in expenses
            if month_key(e.entry_date) == month_key(today)
        )

        trend: list[MonthTrend] = []
        cursor_year, cursor_month = today.year, today.month
        months: list[tuple[int, int]] = []
        for _ in range(6):
            months.append((cursor_year, cursor_month))
            cursor_month -= 1
            if cursor_month == 0:
                cursor_month = 12
                cursor_year -= 1
        months.reverse()

        for year, month in months:
            m_income = sum(
                self._usd_equivalent(float(e.amount), e.currency, bcv_rate)
                for e in incomes
                if month_key(e.entry_date) == (year, month)
            )
            m_expense = sum(
                self._usd_equivalent(float(e.amount), e.currency, bcv_rate)
                for e in expenses
                if month_key(e.entry_date) == (year, month)
            )
            trend.append(MonthTrend(label=MONTH_ABBR_ES[month], income=m_income, expense=m_expense))

        return FinanceDashboard(
            income_month=income_month,
            expense_month=expense_month,
            net_flow=income_month - expense_month,
            bcv_rate=bcv_rate,
            bcv_rate_is_stale=settings.bcv_rate_is_stale,
            trend=trend,
        )

    async def _bcv_rate_as_of(self, value_date: date) -> tuple[float, date | None]:
        """The period's own closing rate — a single, national, date-specific
        rate (ExchangeRate has no filial_id), unlike the per-filial "current"
        rate the finance dashboard still uses. This is what makes a holding-
        wide consolidated report internally consistent."""
        from app.modules.exchange_rates.service import ExchangeRateService

        rate_row = await ExchangeRateService(self.db).as_of("USD", value_date)
        if rate_row is None:
            return 0.0, None
        return float(rate_row.rate_ves), rate_row.value_date

    def _entry_usd_amount(self, entry: "IncomeEntry | ExpenseEntry", bcv_rate: float) -> float:
        """Prefer the entry's own frozen amount_usd (populated by the manual-
        movements flow, at the entry's own date) over re-deriving it with one
        current/period rate — automatic entries never set amount_usd, so
        those still fall back to the plain conversion."""
        if entry.amount_usd is not None:
            return float(entry.amount_usd)
        return self._usd_equivalent(float(entry.amount), entry.currency, bcv_rate)

    async def _vehicle_department(
        self,
        filial_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        condition: "VehicleCondition",
        key: str,
        label: str,
    ) -> tuple[ProfitabilityDepartmentRow, int, int, list[ProfitabilityLineItem]]:
        result = await self.db.execute(
            select(VehicleSale, DealershipVehicle)
            .join(DealershipVehicle, DealershipVehicle.id == VehicleSale.vehicle_id)
            .where(
                VehicleSale.filial_id.in_(filial_ids),
                VehicleSale.created_at >= date_from,
                VehicleSale.created_at <= date_to,
                DealershipVehicle.condition == condition,
            )
        )
        pairs = result.all()
        net_sales = sum(float(sale.final_price) for sale, _ in pairs)
        direct_cost = sum(float(vehicle.cost_price) if vehicle.cost_price is not None else 0.0 for _, vehicle in pairs)
        estimated_count = sum(1 for _, vehicle in pairs if vehicle.cost_is_estimated)

        negative_lines = []
        for sale, vehicle in pairs:
            cost = float(vehicle.cost_price) if vehicle.cost_price is not None else 0.0
            margin = float(sale.final_price) - cost
            if margin < 0:
                negative_lines.append(
                    ProfitabilityLineItem(
                        department_key=key,
                        document_type="vehicle_sale",
                        document_id=sale.id,
                        document_code=sale.code,
                        description=f"{vehicle.brand} {vehicle.model} {vehicle.year} · {sale.client_name}",
                        date=sale.created_at.date(),
                        net_sales=float(sale.final_price),
                        direct_cost=cost,
                        margin=margin,
                        note=sale.below_cost_override_note,
                        authorized_by_user_id=sale.authorized_by_user_id,
                        authorized_at=sale.authorized_at,
                    )
                )

        return (
            _profitability_department_row(key, label, net_sales, direct_cost),
            len(pairs),
            estimated_count,
            negative_lines,
        )

    async def _parts_department(
        self, filial_ids: list[uuid.UUID], date_from: date, date_to: date
    ) -> tuple[ProfitabilityDepartmentRow, list[ProfitabilityLineItem]]:
        # Mostrador only (parts consumed by a service order are folded into
        # "Taller · mano de obra" instead, matching the shop's actual
        # one-document-per-order billing). Cancelled sales are excluded from
        # both sides — the existing cost calc didn't exclude them either,
        # a real (low-risk) fix bundled in here.
        result = await self.db.execute(
            select(PartSaleLine, PartSale)
            .join(PartSale, PartSale.id == PartSaleLine.part_sale_id)
            .where(
                PartSale.filial_id.in_(filial_ids),
                PartSale.created_at >= date_from,
                PartSale.created_at <= date_to,
                PartSale.status != PartSaleStatus.CANCELADO,
            )
        )
        pairs = result.all()
        almacen = AlmacenService(self.db)
        net_sales = sum(float(line.line_total) for line, _ in pairs)
        direct_cost = 0.0
        negative_lines = []
        for line, sale in pairs:
            cost = float(line.unit_cost) if line.unit_cost is not None else (await almacen.get_average_cost(line.part_id) or 0.0)
            line_cost = line.quantity * cost
            direct_cost += line_cost
            margin = float(line.line_total) - line_cost
            if margin < 0:
                part = await self.db.get(Part, line.part_id)
                negative_lines.append(
                    ProfitabilityLineItem(
                        department_key="repuestos",
                        document_type="part_sale",
                        document_id=sale.id,
                        document_code=sale.code,
                        description=part.name if part else "Repuesto",
                        date=sale.created_at.date(),
                        net_sales=float(line.line_total),
                        direct_cost=line_cost,
                        margin=margin,
                    )
                )
        return _profitability_department_row("repuestos", "Repuestos", net_sales, direct_cost), negative_lines

    async def _taller_department(
        self, filial_ids: list[uuid.UUID], date_from: date, date_to: date
    ) -> tuple[ProfitabilityDepartmentRow, list[ProfitabilityLineItem]]:
        # Revenue = the full invoiced total at issuance — accrual, not cash
        # collected — so a pending receivable still counts in the period it
        # was actually billed in. ServiceOrderInvoice has no filial_id of its
        # own; join through ServiceOrder for it.
        result = await self.db.execute(
            select(ServiceOrderInvoice, ServiceOrder)
            .join(ServiceOrder, ServiceOrder.id == ServiceOrderInvoice.service_order_id)
            .where(
                ServiceOrder.filial_id.in_(filial_ids),
                ServiceOrderInvoice.issued_at >= date_from,
                ServiceOrderInvoice.issued_at <= date_to,
            )
        )
        pairs = result.all()
        net_sales = sum(float(invoice.total_usd) for invoice, _ in pairs)

        # Cost is bucketed per order — includes every dispatched line
        # regardless of payer, since a comeback/warranty-covered part still
        # costs the shop even though it isn't billed to the client. That's
        # exactly what can make a single order's own margin negative even
        # though the department total nets out positive.
        cost_by_order: dict[uuid.UUID, float] = {}
        if pairs:
            order_ids = [order.id for _, order in pairs]
            lines_result = await self.db.execute(
                select(ServiceOrderTransferLine, ServiceOrderTransfer.service_order_id)
                .join(ServiceOrderTransfer, ServiceOrderTransfer.id == ServiceOrderTransferLine.transfer_id)
                .where(
                    ServiceOrderTransfer.service_order_id.in_(order_ids),
                    ServiceOrderTransfer.status == TransferStatus.PEDIDO,
                )
            )
            transfer_lines = lines_result.all()
            line_ids = [line.id for line, _ in transfer_lines]
            allocation_cost_by_line: dict[uuid.UUID, float] = {}
            if line_ids:
                allocation_result = await self.db.execute(
                    select(ServiceOrderTransferLotAllocation).where(
                        ServiceOrderTransferLotAllocation.transfer_line_id.in_(line_ids)
                    )
                )
                for allocation in allocation_result.scalars().all():
                    allocation_cost_by_line[allocation.transfer_line_id] = allocation_cost_by_line.get(
                        allocation.transfer_line_id, 0.0
                    ) + allocation.quantity * float(allocation.unit_cost)
            for line, service_order_id in transfer_lines:
                line_cost = allocation_cost_by_line.get(line.id, 0.0)
                # Dispatched before F0-01 (or otherwise never allocated to a
                # lot) — the line's own recorded cost is the best real number
                # available; never guess a quantity that isn't there.
                line_cost = line_cost if line_cost > 0 else float(line.cost_total)
                cost_by_order[service_order_id] = cost_by_order.get(service_order_id, 0.0) + line_cost

        direct_cost = sum(cost_by_order.values())

        negative_lines = []
        for invoice, order in pairs:
            cost = cost_by_order.get(order.id, 0.0)
            margin = float(invoice.total_usd) - cost
            if margin < 0:
                negative_lines.append(
                    ProfitabilityLineItem(
                        department_key="taller_mano_obra",
                        document_type="service_order_invoice",
                        document_id=order.id,
                        document_code=order.code,
                        description=f"Factura {invoice.code}",
                        date=invoice.issued_at.date(),
                        net_sales=float(invoice.total_usd),
                        direct_cost=cost,
                        margin=margin,
                    )
                )

        return (
            _profitability_department_row("taller_mano_obra", "Taller · mano de obra", net_sales, direct_cost),
            negative_lines,
        )

    async def _manual_income_department(
        self,
        filial_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        concept: IncomeConcept,
        bcv_rate: float,
        key: str,
        label: str,
    ) -> ProfitabilityDepartmentRow:
        result = await self.db.execute(
            select(IncomeEntry).where(
                IncomeEntry.filial_id.in_(filial_ids),
                IncomeEntry.entry_date >= date_from,
                IncomeEntry.entry_date <= date_to,
                IncomeEntry.concept == concept,
            )
        )
        net_sales = sum(self._entry_usd_amount(entry, bcv_rate) for entry in result.scalars().all())
        return _profitability_department_row(key, label, net_sales, 0.0)

    async def _fx_reexpression_adjustment(
        self, filial_ids: list[uuid.UUID], date_from: date, date_to: date, closing_rate: float
    ) -> float:
        """FX gain/(loss) for the period: re-express every Bs-denominated
        entry's amount at the period's closing rate and diff it against the
        USD equivalent frozen at the entry's own date. Only covers entries
        with a frozen rate — i.e. ones created through the manual-movements
        form; an automatic Bs-denominated entry (e.g. a Bs payment on an ODS
        invoice) never freezes a baseline, so it's excluded here (surfaced as
        a caveat in the UI, not silently wrong)."""
        if not closing_rate:
            return 0.0

        def _diff(entry: "IncomeEntry | ExpenseEntry") -> float:
            reexpressed = float(entry.amount_bs) / closing_rate
            return reexpressed - float(entry.amount_usd)

        income_result = await self.db.execute(
            select(IncomeEntry).where(
                IncomeEntry.filial_id.in_(filial_ids),
                IncomeEntry.entry_date >= date_from,
                IncomeEntry.entry_date <= date_to,
                IncomeEntry.currency == AccountCurrency.BS,
                IncomeEntry.amount_usd.is_not(None),
                IncomeEntry.amount_bs.is_not(None),
            )
        )
        expense_result = await self.db.execute(
            select(ExpenseEntry).where(
                ExpenseEntry.filial_id.in_(filial_ids),
                ExpenseEntry.entry_date >= date_from,
                ExpenseEntry.entry_date <= date_to,
                ExpenseEntry.currency == AccountCurrency.BS,
                ExpenseEntry.amount_usd.is_not(None),
                ExpenseEntry.amount_bs.is_not(None),
            )
        )
        income_diff = sum(_diff(e) for e in income_result.scalars().all())
        expense_diff = sum(_diff(e) for e in expense_result.scalars().all())
        return income_diff - expense_diff

    async def list_receivables(self, filial_id: uuid.UUID) -> list["ReceivableRead"]:
        """Cuentas por Cobrar — every document sold but not yet collected.
        ODS invoices already track this precisely (billing.py's own
        collect_invoice flow); a parts counter sale has no such flow at
        all — this system only ever books its income when it reaches
        COMPLETADO (PartsService.update_sale_status), so any non-cancelled
        sale that hasn't gotten there yet is, by the system's own definition
        of "collected," still outstanding. That's also what makes
        CxC + ingresos reconcile exactly against Rentabilidad's net_sales
        for repuestos, which counts every non-cancelled sale as revenue."""
        from app.modules.service_orders.billing import BillingService
        from app.modules.service_orders.billing_schemas import ReceivableRead

        invoice_receivables = await BillingService(self.db).list_receivables(filial_id)

        result = await self.db.execute(
            select(PartSale)
            .options(selectinload(PartSale.lines))
            .where(
                PartSale.filial_id == filial_id,
                PartSale.status.not_in([PartSaleStatus.COMPLETADO, PartSaleStatus.CANCELADO]),
            )
        )
        today = venezuela_today()
        part_sale_receivables = []
        for sale in result.scalars().all():
            days_outstanding = (today - sale.created_at.date()).days
            total = sale.total
            part_sale_receivables.append(
                ReceivableRead(
                    document_type="part_sale",
                    invoice_id=sale.id,
                    code=sale.code,
                    filial_id=sale.filial_id,
                    billed_client_name=sale.client_name,
                    total_usd=total,
                    iva_retention_amount=0.0,
                    islr_retention_amount=0.0,
                    net_expected=total,
                    amount_paid_at_issuance=0.0,
                    pending_amount=total,
                    issued_at=sale.created_at,
                    days_outstanding=days_outstanding,
                    aging_bucket=aging_bucket(days_outstanding),
                )
            )

        return sorted(invoice_receivables + part_sale_receivables, key=lambda r: r.issued_at)

    async def _compute_profitability(
        self,
        filial_ids: list[uuid.UUID],
        date_from: date,
        date_to: date,
        report_filial_id: uuid.UUID | None,
    ) -> ProfitabilityReport:
        bcv_rate, bcv_rate_date = await self._bcv_rate_as_of(date_to)
        today = venezuela_today()
        period_is_closed = (date_to.year, date_to.month) != (today.year, today.month)

        nuevos_row, nuevos_count, nuevos_estimated, nuevos_negative = await self._vehicle_department(
            filial_ids, date_from, date_to, VehicleCondition.NUEVO, "vehiculos_nuevos", "Vehículos nuevos"
        )
        usados_row, usados_count, usados_estimated, usados_negative = await self._vehicle_department(
            filial_ids, date_from, date_to, VehicleCondition.USADO, "vehiculos_usados", "Vehículos usados"
        )
        repuestos_row, repuestos_negative = await self._parts_department(filial_ids, date_from, date_to)
        taller_row, taller_negative = await self._taller_department(filial_ids, date_from, date_to)
        garantia_marca_row = await self._manual_income_department(
            filial_ids, date_from, date_to, IncomeConcept.GARANTIA_MARCA, bcv_rate,
            "garantia_marca", "Garantía · marca",
        )
        fi_row = await self._manual_income_department(
            filial_ids, date_from, date_to, IncomeConcept.FI_INTERMEDIACION, bcv_rate,
            "fi_intermediacion", "F&I · intermediación",
        )

        negative_margin_lines = [
            *nuevos_negative, *usados_negative, *repuestos_negative, *taller_negative,
        ]

        departments = [nuevos_row, usados_row, repuestos_row, taller_row, garantia_marca_row, fi_row]
        net_sales_total = sum(d.net_sales for d in departments)
        direct_cost_total = sum(d.direct_cost for d in departments)
        gross_profit_total = net_sales_total - direct_cost_total
        gross_margin = gross_profit_total / net_sales_total if net_sales_total else 0.0

        expense_result = await self.db.execute(
            select(ExpenseEntry).where(
                ExpenseEntry.filial_id.in_(filial_ids),
                ExpenseEntry.entry_date >= date_from,
                ExpenseEntry.entry_date <= date_to,
            )
        )
        expenses = list(expense_result.scalars().all())
        operating_expenses = sum(
            self._entry_usd_amount(e, bcv_rate)
            for e in expenses
            if e.category not in (ExpenseCategory.COMPRAS_PROVEEDORES, ExpenseCategory.NOMINA_COMISIONES)
        )
        commissions_paid = sum(
            self._entry_usd_amount(e, bcv_rate) for e in expenses if e.category == ExpenseCategory.NOMINA_COMISIONES
        )

        movements_result = await self.db.execute(
            select(StockMovement).where(
                StockMovement.filial_id.in_(filial_ids),
                StockMovement.movement_type == MovementType.DEVOLUCION,
                StockMovement.created_at >= date_from,
                StockMovement.created_at <= date_to,
            )
        )
        shrinkage_losses = sum(m.quantity * float(m.unit_cost or 0) for m in movements_result.scalars().all())

        fx_adjustment = await self._fx_reexpression_adjustment(filial_ids, date_from, date_to, bcv_rate)

        warranty_cost = 0.0
        for filial_id in filial_ids:
            warranty_cost += await self._warranty_cost(filial_id, date_from, date_to)

        adjustments = [
            ProfitabilityAdjustmentRow(
                key="gastos_operacionales", label="Gastos operacionales",
                origin="Egresos clasificados", amount=-operating_expenses,
            ),
            ProfitabilityAdjustmentRow(
                key="comisiones", label="Comisiones",
                origin="Nómina y comisiones", amount=-commissions_paid,
            ),
            ProfitabilityAdjustmentRow(
                key="mermas_inventario", label="Mermas de inventario",
                origin="Ajustes de almacén", amount=-shrinkage_losses,
            ),
            ProfitabilityAdjustmentRow(
                key="diferencia_cambio", label="Diferencia en cambio",
                origin="Reexpresión de saldos", amount=fx_adjustment,
            ),
            ProfitabilityAdjustmentRow(
                key="costo_garantia_taller", label="Costo de garantía (taller)",
                origin="Retrabajos cubiertos por el taller", amount=-warranty_cost,
            ),
        ]
        net_profit = gross_profit_total + sum(a.amount for a in adjustments)
        net_margin = net_profit / net_sales_total if net_sales_total else 0.0

        from app.modules.kpis.service import KpiService

        kpi_service = KpiService(self.db)
        manual_count = 0
        manual_total = 0
        for filial_id in filial_ids:
            rate = await kpi_service.get_manual_movements_rate(filial_id, date_from, date_to)
            manual_count += rate.manual_count
            manual_total += rate.total_count
        manual_movements_rate = manual_count / manual_total if manual_total else 0.0

        return ProfitabilityReport(
            period_label=f"{date_from.isoformat()} – {date_to.isoformat()}",
            filial_id=report_filial_id,
            date_from=date_from,
            date_to=date_to,
            bcv_rate=bcv_rate,
            bcv_rate_date=bcv_rate_date,
            period_is_closed=period_is_closed,
            departments=departments,
            net_sales_total=net_sales_total,
            direct_cost_total=direct_cost_total,
            gross_profit_total=gross_profit_total,
            gross_margin=gross_margin,
            adjustments=adjustments,
            net_profit=net_profit,
            net_margin=net_margin,
            negative_margin_lines=negative_margin_lines,
            vehicles_sold_count=nuevos_count + usados_count,
            vehicles_with_estimated_cost_count=nuevos_estimated + usados_estimated,
            manual_movements_rate=manual_movements_rate,
            manual_movements_count=manual_count,
            manual_movements_total_count=manual_total,
        )

    async def get_profitability(
        self, filial_id: uuid.UUID, date_from: date, date_to: date
    ) -> ProfitabilityReport:
        return await self._compute_profitability([filial_id], date_from, date_to, filial_id)

    async def get_profitability_for_holding(
        self, holding_id: uuid.UUID, date_from: date, date_to: date
    ) -> ProfitabilityReport:
        result = await self.db.execute(select(Filial.id).where(Filial.holding_id == holding_id))
        filial_ids = list(result.scalars().all())
        return await self._compute_profitability(filial_ids, date_from, date_to, None)

    async def _warranty_cost(self, filial_id: uuid.UUID, date_from: date, date_to: date) -> float:
        """What warranty claims the SHOP itself absorbs actually cost it: the
        real FIFO cost of parts consumed (traced via
        ServiceOrderTransferLotAllocation — F0-01) plus the labor of the
        flagged service, at the current hourly rate. Free to the client, but
        not to the shop — parts leave inventory and the technician's hours
        are paid regardless. Only claim_type in (comeback, repuesto_proveedor)
        count here — fábrica/campaña claims are the manufacturer's money,
        not the shop's rework cost."""
        claims_result = await self.db.execute(
            select(WarrantyClaim).where(
                WarrantyClaim.filial_id == filial_id,
                WarrantyClaim.claim_type.in_([WarrantyClaimType.COMEBACK, WarrantyClaimType.REPUESTO_PROVEEDOR]),
                WarrantyClaim.claimed_at >= date_from,
                WarrantyClaim.claimed_at <= date_to,
            )
        )
        claims = list(claims_result.scalars().all())
        if not claims:
            return 0.0

        settings = await PostVentasService(self.db).get_labor_settings(filial_id)
        hourly_rate = float(settings.hourly_rate)

        total = 0.0
        for claim in claims:
            if claim.part_id is not None:
                line_result = await self.db.execute(
                    select(ServiceOrderTransferLine)
                    .join(ServiceOrderTransfer, ServiceOrderTransfer.id == ServiceOrderTransferLine.transfer_id)
                    .where(
                        ServiceOrderTransfer.service_order_id == claim.service_order_id,
                        ServiceOrderTransferLine.part_id == claim.part_id,
                    )
                )
                lines = list(line_result.scalars().all())
                line_ids = [line.id for line in lines]
                allocation_cost = 0.0
                if line_ids:
                    allocation_result = await self.db.execute(
                        select(ServiceOrderTransferLotAllocation).where(
                            ServiceOrderTransferLotAllocation.transfer_line_id.in_(line_ids)
                        )
                    )
                    allocation_cost = sum(
                        a.quantity * float(a.unit_cost) for a in allocation_result.scalars().all()
                    )
                if allocation_cost > 0:
                    total += allocation_cost
                else:
                    # Dispatched before F0-01 (or otherwise never allocated to a
                    # lot) — the line's own recorded cost is the best real
                    # number available; never guess a quantity that isn't there.
                    total += sum(float(line.cost_total) for line in lines)

            if claim.tempario_id is not None:
                tempario = await self.db.get(Tempario, claim.tempario_id)
                if tempario is not None:
                    total += float(tempario.estimated_hours) * hourly_rate

        return total
