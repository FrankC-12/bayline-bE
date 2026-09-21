import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.administracion.schemas import ProfitabilityReport
from app.modules.auth.dependencies import require_holding_user, require_platform_user
from app.modules.auth.exceptions import InsufficientPermissionsError
from app.modules.auth.schemas import CurrentUser
from app.modules.holdings.schemas import (
    HoldingCreate,
    HoldingDashboardReport,
    HoldingRead,
    HoldingUpdate,
)
from app.modules.holdings.service import HoldingService
from app.modules.service_orders.billing_schemas import HoldingWarrantyReceivablesReport

router = APIRouter(prefix="/holdings", tags=["Holdings"])


def _ensure_owns_holding(current_user: CurrentUser, holding_id: uuid.UUID) -> None:
    if current_user.holding_id != holding_id:
        raise InsufficientPermissionsError()


def get_holding_service(db: AsyncSession = Depends(get_db)) -> HoldingService:
    return HoldingService(db)


@router.get("", response_model=list[HoldingRead])
async def list_holdings(
    current_user: CurrentUser = Depends(require_platform_user),
    service: HoldingService = Depends(get_holding_service),
) -> list[HoldingRead]:
    """List every holding. Platform-only, same as create/update/activate —
    the only consumer is the platform-level Holdings screen; a holding or
    filial caller has no legitimate reason to enumerate every tenant."""
    return await service.list_holdings()


@router.get("/{holding_id}", response_model=HoldingRead)
async def get_holding(
    holding_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_platform_user),
    service: HoldingService = Depends(get_holding_service),
) -> HoldingRead:
    """Retrieve a single holding by its id. Platform-only — unlike the
    holding-scoped sub-resources below (garantías, rentabilidad), nothing
    in the app fetches a bare holding record by id for a holding-level
    caller, so there's no self-service case to preserve here."""
    return await service.get_holding(holding_id)


@router.post("", response_model=HoldingRead, status_code=status.HTTP_201_CREATED)
async def create_holding(
    payload: HoldingCreate,
    current_user: CurrentUser = Depends(require_platform_user),
    service: HoldingService = Depends(get_holding_service),
) -> HoldingRead:
    """Create a new holding. Platform-only."""
    return await service.create_holding(payload)


@router.patch("/{holding_id}", response_model=HoldingRead)
async def update_holding(
    holding_id: uuid.UUID,
    payload: HoldingUpdate,
    current_user: CurrentUser = Depends(require_platform_user),
    service: HoldingService = Depends(get_holding_service),
) -> HoldingRead:
    """Update a holding's name or slug. Platform-only."""
    return await service.update_holding(holding_id, payload)


@router.post("/{holding_id}/activate", response_model=HoldingRead)
async def activate_holding(
    holding_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_platform_user),
    service: HoldingService = Depends(get_holding_service),
) -> HoldingRead:
    """Reactivate a previously deactivated holding. Platform-only."""
    return await service.set_active_status(holding_id, is_active=True)


@router.post("/{holding_id}/deactivate", response_model=HoldingRead)
async def deactivate_holding(
    holding_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_platform_user),
    service: HoldingService = Depends(get_holding_service),
) -> HoldingRead:
    """Deactivate a holding without deleting its historical data. Platform-only."""
    return await service.set_active_status(holding_id, is_active=False)


@router.get("/{holding_id}/dashboard", response_model=HoldingDashboardReport)
async def get_holding_dashboard(
    holding_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_holding_user),
    db: AsyncSession = Depends(get_db),
) -> HoldingDashboardReport:
    """Per-filial KPI rollup (ODS, ODT, ventas, clientes, usuarios,
    almacenes) for the holding's own summary dashboard. Only the owning
    Holding may view it."""
    from app.modules.holdings.dashboard import HoldingDashboardService

    _ensure_owns_holding(current_user, holding_id)
    return await HoldingDashboardService(db).get_dashboard(holding_id)


@router.get("/{holding_id}/garantias-consolidado", response_model=HoldingWarrantyReceivablesReport)
async def get_warranty_receivables_report(
    holding_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_holding_user),
    db: AsyncSession = Depends(get_db),
) -> HoldingWarrantyReceivablesReport:
    """Per-filial cuentas por cobrar for invoices billed to the holding's own
    client record (factory-warranty work). Only the owning Holding may view it."""
    from app.modules.service_orders.billing import BillingService

    _ensure_owns_holding(current_user, holding_id)
    return await BillingService(db).get_holding_warranty_receivables(holding_id)


@router.get("/{holding_id}/finance/profitability", response_model=ProfitabilityReport)
async def get_holding_profitability(
    holding_id: uuid.UUID,
    date_from: date = Query(...),
    date_to: date = Query(...),
    current_user: CurrentUser = Depends(require_holding_user),
    db: AsyncSession = Depends(get_db),
) -> ProfitabilityReport:
    """Rentabilidad consolidated across every filial in the holding. Only the
    owning Holding may view it — not gated by ensure_module_access, which is
    filial-scoped by construction and always rejects a holding-scope caller."""
    from app.modules.administracion.service import AdministracionService

    _ensure_owns_holding(current_user, holding_id)
    return await AdministracionService(db).get_profitability_for_holding(holding_id, date_from, date_to)
