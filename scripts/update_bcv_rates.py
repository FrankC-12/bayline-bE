"""Refresh BCV rates. Intended for a daily cron entry."""

import asyncio

from app.core.database import AsyncSessionLocal
from app.modules.exchange_rates.service import ExchangeRateService


async def main() -> None:
    async with AsyncSessionLocal() as session:
        rates = await ExchangeRateService(session).refresh()
        print(", ".join(f"{rate.currency}={rate.rate_ves}" for rate in rates))


if __name__ == "__main__":
    asyncio.run(main())
