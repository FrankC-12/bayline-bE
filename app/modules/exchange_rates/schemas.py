from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class ExchangeRateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    currency: str
    rate_ves: float
    value_date: date
    source: str
    fetched_at: datetime


class ExchangeRatesResponse(BaseModel):
    rates: list[ExchangeRateRead]
