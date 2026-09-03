import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.administracion.schemas import (
    AccountCreate,
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
    SupplierClaimUpdate,
    SupplierCreate,
    SupplierDetailRead,
    SupplierRead,
    SupplierUpdate,
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
    return await service.update_request_status(request_id, payload.status, payload.quotes, payload.warehouse_id)


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


# Income / Expense


@router.get("/income-entries", response_model=list[IncomeEntryRead])
async def list_income_entries(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> list[IncomeEntryRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_income(filial_id, search)


@router.post("/income-entries", response_model=IncomeEntryRead, status_code=status.HTTP_201_CREATED)
async def create_income_entry(
    payload: IncomeEntryCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> IncomeEntryRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_income(payload, current_user.user_id)


@router.get("/expense-entries", response_model=list[ExpenseEntryRead])
async def list_expense_entries(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> list[ExpenseEntryRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_expenses(filial_id, search)


@router.post("/expense-entries", response_model=ExpenseEntryRead, status_code=status.HTTP_201_CREATED)
async def create_expense_entry(
    payload: ExpenseEntryCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: AdministracionService = Depends(get_service),
) -> ExpenseEntryRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_expense(payload, current_user.user_id)


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
