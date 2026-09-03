from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.exchange_rates.schemas import ExchangeRatesResponse
from app.modules.exchange_rates.service import ExchangeRateService

router = APIRouter(prefix="/exchange-rates", tags=["Exchange rates"])


@router.get("/latest", response_model=ExchangeRatesResponse)
async def latest_rates(
    _: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> ExchangeRatesResponse:
    return ExchangeRatesResponse(rates=await ExchangeRateService(db).latest())
