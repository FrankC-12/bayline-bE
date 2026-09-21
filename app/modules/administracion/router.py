import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.administracion.enums import AccountCurrency, CounterpartyType, ExpenseCategory, IncomeConcept
from app.modules.administracion.exceptions import EntryNotFoundError
from app.modules.administracion.models import ExpenseEntry, IncomeEntry
from app.modules.administracion.schemas import (
    AccountCreate,
    AccountMovementRead,
    AccountRead,
    AccountUpdate,
    ExpenseEntryCreate,
    ExpenseEntryRead,
    FinanceDashboard,
    IncomeEntryCreate,
    IncomeEntryRead,
    ProfitabilityReport,
    PurchaseRequestCreate,
    PurchaseRequestRead,
    PurchaseRequestStatusUpdate,
    SupplierClaimCreate,
    SupplierClaimRead,
    SupplierClaimResolveInput,
    SupplierClaimUpdate,
    SupplierCreate,
    SupplierDetailRead,
    SupplierRead,
    SupplierUpdate,
    WarrantySubmissionCreate,
    WarrantySubmissionPayInput,
    WarrantySubmissionRead,
)
from app.modules.administracion.service import AdministracionService
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access

MODULE_ID = "administracion"

router = APIRouter(tags=["Administracion"])


def get_service(db: AsyncSession = Depends(get_db)) -> AdministracionService:
    return AdministracionService(db)


async def _ensure_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, MODULE_ID, level)


# Suppliers


@router.get("/suppliers", response_model=list[SupplierRead])
async def list_suppliers(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> list[SupplierRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_suppliers(filial_id, search)


@router.post("/suppliers", response_model=SupplierRead, status_code=status.HTTP_201_CREATED)
async def create_supplier(
    payload: SupplierCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> SupplierRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_supplier(payload)


@router.get("/suppliers/{supplier_id}", response_model=SupplierDetailRead)
async def get_supplier_detail(
    supplier_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> SupplierDetailRead:
    existing = await service.get_supplier(supplier_id)
    await _ensure_access(current_user, existing.filial_id, service.db)
    return await service.get_supplier_detail(supplier_id)


@router.patch("/suppliers/{supplier_id}", response_model=SupplierRead)
async def update_supplier(
    supplier_id: uuid.UUID,
    payload: SupplierUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> SupplierRead:
    existing = await service.get_supplier(supplier_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_supplier(supplier_id, payload)


# Purchase requests


@router.get("/purchase-requests", response_model=list[PurchaseRequestRead])
async def list_purchase_requests(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> list[PurchaseRequestRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_requests(filial_id, search)


@router.get("/purchase-requests/{request_id}", response_model=PurchaseRequestRead)
async def get_purchase_request(
    request_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> PurchaseRequestRead:
    request = await service.get_request(request_id)
    await _ensure_access(current_user, request.filial_id, service.db)
    return request


@router.post("/purchase-requests", response_model=PurchaseRequestRead, status_code=status.HTTP_201_CREATED)
async def create_purchase_request(
    payload: PurchaseRequestCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> PurchaseRequestRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_request(payload)


@router.patch("/purchase-requests/{request_id}", response_model=PurchaseRequestRead)
async def update_purchase_request_status(
    request_id: uuid.UUID,
    payload: PurchaseRequestStatusUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> PurchaseRequestRead:
    existing = await service.get_request(request_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_request_status(
        request_id, payload.status, payload.quotes, payload.warehouse_id, payload.location,
        current_user.user_id,
    )


# Supplier claims


@router.get("/supplier-claims", response_model=list[SupplierClaimRead])
async def list_supplier_claims(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> list[SupplierClaimRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_claims(filial_id)


@router.post("/supplier-claims", response_model=SupplierClaimRead, status_code=status.HTTP_201_CREATED)
async def create_supplier_claim(
    payload: SupplierClaimCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> SupplierClaimRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_claim(payload)


@router.patch("/supplier-claims/{claim_id}", response_model=SupplierClaimRead)
async def update_supplier_claim(
    claim_id: uuid.UUID,
    payload: SupplierClaimUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> SupplierClaimRead:
    existing = await service.get_claim(claim_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_claim(claim_id, payload)


@router.post("/supplier-claims/{claim_id}/resolve", response_model=SupplierClaimRead)
async def resolve_supplier_claim(
    claim_id: uuid.UUID,
    payload: SupplierClaimResolveInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> SupplierClaimRead:
    existing = await service.get_claim(claim_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.resolve_claim(claim_id, payload, current_user.user_id)


# Warranty submissions


@router.get("/warranty-submissions", response_model=list[WarrantySubmissionRead])
async def list_warranty_submissions(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> list[WarrantySubmissionRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_warranty_submissions(filial_id)


@router.post("/warranty-submissions", response_model=WarrantySubmissionRead, status_code=status.HTTP_201_CREATED)
async def create_warranty_submission(
    payload: WarrantySubmissionCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> WarrantySubmissionRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_warranty_submission(payload)


@router.get("/warranty-submissions/{submission_id}", response_model=WarrantySubmissionRead)
async def get_warranty_submission(
    submission_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> WarrantySubmissionRead:
    submission = await service.get_warranty_submission(submission_id)
    await _ensure_access(current_user, submission.filial_id, service.db)
    return submission


@router.post("/warranty-submissions/{submission_id}/refresh", response_model=WarrantySubmissionRead)
async def refresh_warranty_submission(
    submission_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> WarrantySubmissionRead:
    existing = await service.get_warranty_submission(submission_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.refresh_warranty_submission(submission_id)


@router.post("/warranty-submissions/{submission_id}/submit", response_model=WarrantySubmissionRead)
async def submit_warranty_submission(
    submission_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> WarrantySubmissionRead:
    existing = await service.get_warranty_submission(submission_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.submit_warranty_submission(submission_id, current_user.user_id)


@router.post("/warranty-submissions/{submission_id}/pay", response_model=WarrantySubmissionRead)
async def pay_warranty_submission(
    submission_id: uuid.UUID,
    payload: WarrantySubmissionPayInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> WarrantySubmissionRead:
    existing = await service.get_warranty_submission(submission_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.mark_warranty_submission_paid(submission_id, payload, current_user.user_id)


@router.delete("/warranty-submissions/{submission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_warranty_submission(
    submission_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> None:
    existing = await service.get_warranty_submission(submission_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    await service.delete_warranty_submission(submission_id)


@router.get("/warranty-submissions/{submission_id}/export")
async def export_warranty_submission(
    submission_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> Response:
    existing = await service.get_warranty_submission(submission_id)
    await _ensure_access(current_user, existing.filial_id, service.db)
    content, filename = await service.export_warranty_submission_csv(submission_id)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Accounts


@router.get("/accounts", response_model=list[AccountRead])
async def list_accounts(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> list[AccountRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_accounts(filial_id)


@router.post("/accounts", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AccountCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> AccountRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    account = await service.create_account(payload)
    accounts = await service.list_accounts(payload.filial_id)
    return next(a for a in accounts if a["id"] == account.id)


@router.patch("/accounts/{account_id}", response_model=AccountRead)
async def update_account(
    account_id: uuid.UUID,
    payload: AccountUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> AccountRead:
    existing = await service.get_account(account_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    updated = await service.update_account(account_id, payload)
    accounts = await service.list_accounts(updated.filial_id)
    return next(a for a in accounts if a["id"] == updated.id)


@router.get("/accounts/{account_id}", response_model=AccountRead)
async def get_account_detail(
    account_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> AccountRead:
    existing = await service.get_account(account_id)
    await _ensure_access(current_user, existing.filial_id, service.db)
    return await service.get_account_detail(account_id)


@router.get("/accounts/{account_id}/movements", response_model=list[AccountMovementRead])
async def get_account_movements(
    account_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> list[AccountMovementRead]:
    existing = await service.get_account(account_id)
    # Same gate as Ingresos/Egresos themselves — a movement's concept,
    # counterparty and attachment are Finanzas-manual detail, not plain
    # "administracion" visibility.
    await _ensure_manual_movement_access(current_user, existing.filial_id, service.db)
    return await service.get_account_movements(account_id, limit)


# Income / Expense


# Manual movements (Ingresos/Egresos) — gated by their own module, separate
# from the rest of Finanzas: someone with general "administracion" access
# does not automatically get this; it's granted per-user via the existing
# permission-override screen.
MANUAL_MOVEMENTS_MODULE_ID = "movimientos-manuales"


async def _ensure_manual_movement_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, MANUAL_MOVEMENTS_MODULE_ID, level)


@router.get("/income-entries", response_model=list[IncomeEntryRead])
async def list_income_entries(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> list[IncomeEntryRead]:
    await _ensure_manual_movement_access(current_user, filial_id, service.db)
    return await service.list_income(filial_id, search)


@router.post("/income-entries", response_model=IncomeEntryRead, status_code=status.HTTP_201_CREATED)
async def create_income_entry(
    filial_id: uuid.UUID = Form(...),
    entry_date: date = Form(...),
    concept: IncomeConcept = Form(...),
    description: str = Form(...),
    amount: float = Form(...),
    currency: AccountCurrency = Form(...),
    account_id: uuid.UUID = Form(...),
    counterparty_type: CounterpartyType = Form(...),
    counterparty_client_id: uuid.UUID | None = Form(None),
    counterparty_supplier_id: uuid.UUID | None = Form(None),
    counterparty_name: str | None = Form(None),
    reference: str | None = Form(None),
    attachment: UploadFile | None = File(None),
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> IncomeEntryRead:
    payload = IncomeEntryCreate(
        filial_id=filial_id, entry_date=entry_date, concept=concept, description=description,
        amount=amount, currency=currency, account_id=account_id, counterparty_type=counterparty_type,
        counterparty_client_id=counterparty_client_id, counterparty_supplier_id=counterparty_supplier_id,
        counterparty_name=counterparty_name, reference=reference,
    )
    await _ensure_manual_movement_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_income(payload, attachment, current_user.user_id)


@router.post("/income-entries/{entry_id}/reverse", response_model=IncomeEntryRead)
async def reverse_income_entry(
    entry_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> IncomeEntryRead:
    existing = await service.db.get(IncomeEntry, entry_id)
    if existing is None:
        raise EntryNotFoundError(str(entry_id))
    await _ensure_manual_movement_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.reverse_income(entry_id, current_user.user_id)


@router.get("/expense-entries", response_model=list[ExpenseEntryRead])
async def list_expense_entries(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> list[ExpenseEntryRead]:
    await _ensure_manual_movement_access(current_user, filial_id, service.db)
    return await service.list_expenses(filial_id, search)


@router.post("/expense-entries", response_model=ExpenseEntryRead, status_code=status.HTTP_201_CREATED)
async def create_expense_entry(
    filial_id: uuid.UUID = Form(...),
    entry_date: date = Form(...),
    category: ExpenseCategory = Form(...),
    beneficiary: str = Form(...),
    description: str = Form(...),
    amount: float = Form(...),
    currency: AccountCurrency = Form(...),
    account_id: uuid.UUID = Form(...),
    counterparty_type: CounterpartyType = Form(...),
    counterparty_client_id: uuid.UUID | None = Form(None),
    counterparty_supplier_id: uuid.UUID | None = Form(None),
    counterparty_name: str | None = Form(None),
    reference: str | None = Form(None),
    attachment: UploadFile | None = File(None),
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> ExpenseEntryRead:
    payload = ExpenseEntryCreate(
        filial_id=filial_id, entry_date=entry_date, category=category, beneficiary=beneficiary,
        description=description, amount=amount, currency=currency, account_id=account_id,
        counterparty_type=counterparty_type, counterparty_client_id=counterparty_client_id,
        counterparty_supplier_id=counterparty_supplier_id, counterparty_name=counterparty_name,
        reference=reference,
    )
    await _ensure_manual_movement_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_expense(payload, attachment, current_user.user_id)


@router.post("/expense-entries/{entry_id}/reverse", response_model=ExpenseEntryRead)
async def reverse_expense_entry(
    entry_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> ExpenseEntryRead:
    existing = await service.db.get(ExpenseEntry, entry_id)
    if existing is None:
        raise EntryNotFoundError(str(entry_id))
    await _ensure_manual_movement_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.reverse_expense(entry_id, current_user.user_id)


# Reports


@router.get("/finance/dashboard", response_model=FinanceDashboard)
async def get_finance_dashboard(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> FinanceDashboard:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.get_dashboard(filial_id)


@router.get("/finance/profitability", response_model=ProfitabilityReport)
async def get_profitability(
    filial_id: uuid.UUID = Query(...),
    date_from: date = Query(...),
    date_to: date = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> ProfitabilityReport:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.get_profitability(filial_id, date_from, date_to)
