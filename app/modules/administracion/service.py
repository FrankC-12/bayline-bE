import uuid
from calendar import month_abbr
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.administracion.enums import (
    AccountCurrency,
    ExpenseCategory,
    IncomeSource,
    PurchaseRequestStatus,
)
from app.modules.administracion.exceptions import (
    AccountNotFoundError,
    ClaimNotFoundError,
    InvalidPurchaseStatusTransitionError,
    PurchaseRequestNotFoundError,
    QuoteRequiredError,
    SupplierNotFoundError,
    WarehouseRequiredError,
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
)
from app.modules.administracion.schemas import (
    AccountCreate,
    AccountUpdate,
    FinanceDashboard,
    MonthTrend,
    ProfitabilityReport,
    PurchaseRequestCreate,
    PurchaseRequestLineRead,
    PurchaseRequestRead,
    SupplierClaimCreate,
    SupplierClaimUpdate,
    SupplierCreate,
    SupplierDetailRead,
    SupplierRead,
    SupplierUpdate,
)
from app.modules.warehouse.enums import MovementType
from app.modules.warehouse.models import StockMovement
from app.modules.warehouse.schemas import LotLineInput
from app.modules.warehouse.service import AlmacenService
from app.modules.concesionario.models import DealershipVehicle, VehicleSale
from app.modules.parts.models import PartSale, PartSaleLine
from app.modules.post_ventas.models import LaborSettings

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
                        LotLineInput(part_id=line.part_id, quantity=line.quantity, unit_cost=float(line.unit_cost)),
                        note=f"Compra {request.code}",
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
        return list(result.scalars().all())

    async def get_claim(self, claim_id: uuid.UUID) -> SupplierClaim:
        claim = await self.db.get(SupplierClaim, claim_id)
        if claim is None:
            raise ClaimNotFoundError(str(claim_id))
        return claim

    async def create_claim(self, payload: SupplierClaimCreate) -> SupplierClaim:
        claim = SupplierClaim(**payload.model_dump())
        self.db.add(claim)
        await self.db.commit()
        await self.db.refresh(claim)
        return claim

    async def update_claim(self, claim_id: uuid.UUID, payload: SupplierClaimUpdate) -> SupplierClaim:
        claim = await self.get_claim(claim_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(claim, field, value)
        await self.db.commit()
        await self.db.refresh(claim)
        return claim

    # Accounts

    async def _get_bcv_rate(self, filial_id: uuid.UUID) -> float:
        result = await self.db.execute(select(LaborSettings).where(LaborSettings.filial_id == filial_id))
        settings = result.scalar_one_or_none()
        return float(settings.bcv_rate) if settings and settings.bcv_rate else 0.0

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
        balance = float(income_result.scalar() or 0) - float(expense_result.scalar() or 0)
        balance_usd = balance if account.currency == AccountCurrency.USD else (balance / bcv_rate if bcv_rate else 0.0)
        return balance, balance_usd

    async def list_accounts(self, filial_id: uuid.UUID) -> list[dict]:
        result = await self.db.execute(
            select(Account).where(Account.filial_id == filial_id).order_by(Account.created_at)
        )
        accounts = list(result.scalars().all())
        bcv_rate = await self._get_bcv_rate(filial_id)
        rows = []
        for account in accounts:
            balance, balance_usd = await self._account_balance(account, bcv_rate)
            rows.append(
                {
                    "id": account.id,
                    "filial_id": account.filial_id,
                    "name": account.name,
                    "bank": account.bank,
                    "currency": account.currency,
                    "account_type": account.account_type,
                    "is_active": account.is_active,
                    "balance": balance,
                    "balance_usd": balance_usd,
                    "created_at": account.created_at,
                }
            )
        return rows

    async def get_account(self, account_id: uuid.UUID) -> Account:
        account = await self.db.get(Account, account_id)
        if account is None:
            raise AccountNotFoundError(str(account_id))
        return account

    async def record_automatic_income(
        self, filial_id: uuid.UUID, description: str, amount: float, origin_reference: str
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
            entry_date=date.today(),
            source=IncomeSource.AUTOMATICO,
            origin_reference=origin_reference,
            description=description,
            amount=amount,
            currency=AccountCurrency.USD,
            account_id=account.id,
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

    async def create_income(self, payload, responsible_user_id: uuid.UUID | None) -> IncomeEntry:
        entry = IncomeEntry(**payload.model_dump(), registered_by_user_id=responsible_user_id)
        self.db.add(entry)
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

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

    async def create_expense(self, payload, responsible_user_id: uuid.UUID | None) -> ExpenseEntry:
        entry = ExpenseEntry(**payload.model_dump(), registered_by_user_id=responsible_user_id)
        self.db.add(entry)
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    # Reports

    def _usd_equivalent(self, amount: float, currency: AccountCurrency, bcv_rate: float) -> float:
        if currency == AccountCurrency.USD:
            return amount
        return amount / bcv_rate if bcv_rate else 0.0

    async def get_dashboard(self, filial_id: uuid.UUID) -> FinanceDashboard:
        bcv_rate = await self._get_bcv_rate(filial_id)
        today = date.today()

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
            trend.append(MonthTrend(label=month_abbr[month].capitalize(), income=m_income, expense=m_expense))

        return FinanceDashboard(
            income_month=income_month,
            expense_month=expense_month,
            net_flow=income_month - expense_month,
            bcv_rate=bcv_rate,
            trend=trend,
        )

    async def get_profitability(
        self, filial_id: uuid.UUID, date_from: date, date_to: date
    ) -> ProfitabilityReport:
        bcv_rate = await self._get_bcv_rate(filial_id)

        income_result = await self.db.execute(
            select(IncomeEntry).where(
                IncomeEntry.filial_id == filial_id,
                IncomeEntry.entry_date >= date_from,
                IncomeEntry.entry_date <= date_to,
            )
        )
        total_income = sum(
            self._usd_equivalent(float(e.amount), e.currency, bcv_rate) for e in income_result.scalars().all()
        )

        expense_result = await self.db.execute(
            select(ExpenseEntry).where(
                ExpenseEntry.filial_id == filial_id,
                ExpenseEntry.entry_date >= date_from,
                ExpenseEntry.entry_date <= date_to,
            )
        )
        expenses = list(expense_result.scalars().all())
        operating_expenses = sum(
            self._usd_equivalent(float(e.amount), e.currency, bcv_rate)
            for e in expenses
            if e.category not in (ExpenseCategory.COMPRAS_PROVEEDORES, ExpenseCategory.NOMINA_COMISIONES)
        )
        commissions_paid = sum(
            self._usd_equivalent(float(e.amount), e.currency, bcv_rate)
            for e in expenses
            if e.category == ExpenseCategory.NOMINA_COMISIONES
        )

        # Parts cost: uses the cost snapshotted on each line at the moment of
        # sale. Older sales made before this snapshot existed fall back to
        # Almacén's current average cost (best guess we have for those).
        sale_lines_result = await self.db.execute(
            select(PartSaleLine)
            .join(PartSale, PartSale.id == PartSaleLine.part_sale_id)
            .where(PartSale.filial_id == filial_id, PartSale.created_at >= date_from, PartSale.created_at <= date_to)
        )
        sale_lines = list(sale_lines_result.scalars().all())
        almacen = AlmacenService(self.db)
        parts_cost = 0.0
        for line in sale_lines:
            if line.unit_cost is not None:
                cost = float(line.unit_cost)
            else:
                cost = await almacen.get_average_cost(line.part_id) or 0.0
            parts_cost += line.quantity * cost

        # Vehicle cost: sum of each sold vehicle's own cost_price, if set.
        vehicle_sales_result = await self.db.execute(
            select(VehicleSale).where(
                VehicleSale.filial_id == filial_id,
                VehicleSale.created_at >= date_from,
                VehicleSale.created_at <= date_to,
            )
        )
        vehicle_sales = list(vehicle_sales_result.scalars().all())
        vehicles_cost = 0.0
        for sale in vehicle_sales:
            vehicle = await self.db.get(DealershipVehicle, sale.vehicle_id)
            if vehicle is not None and vehicle.cost_price is not None:
                vehicles_cost += float(vehicle.cost_price)

        # Shrinkage: stock written off as a supplier return/loss, valued at its FIFO cost.
        movements_result = await self.db.execute(
            select(StockMovement).where(
                StockMovement.filial_id == filial_id,
                StockMovement.movement_type == MovementType.DEVOLUCION,
                StockMovement.created_at >= date_from,
                StockMovement.created_at <= date_to,
            )
        )
        shrinkage_losses = sum(
            m.quantity * float(m.unit_cost or 0) for m in movements_result.scalars().all()
        )

        gross_profit = total_income - parts_cost - vehicles_cost
        net_profit = gross_profit - operating_expenses - commissions_paid - shrinkage_losses

        return ProfitabilityReport(
            period_label=f"{date_from.isoformat()} – {date_to.isoformat()}",
            total_income=total_income,
            parts_cost=parts_cost,
            vehicles_cost=vehicles_cost,
            gross_profit=gross_profit,
            operating_expenses=operating_expenses,
            commissions_paid=commissions_paid,
            shrinkage_losses=shrinkage_losses,
            net_profit=net_profit,
        )
