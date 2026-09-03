import asyncio
import html
import re
import subprocess
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.exchange_rates.models import ExchangeRate

BCV_URL = "https://www.bcv.org.ve/"


def _parse_decimal(value: str) -> float:
    return float(value.strip().replace(".", "").replace(",", "."))


def parse_bcv_html(document: str) -> tuple[date, dict[str, float]]:
    document = html.unescape(document)
    date_match = re.search(
        r'Fecha\s+Valor:.*?property="dc:date"[^>]+content="(\d{4}-\d{2}-\d{2})T',
        document,
        re.DOTALL | re.IGNORECASE,
    )
    if not date_match:
        raise ValueError("BCV response does not contain a value date")

    rates: dict[str, float] = {}
    for element_id, currency in (("dolar", "USD"), ("euro", "EUR")):
        match = re.search(
            rf'<div\s+id="{element_id}"[^>]*>.*?<span>\s*{currency}\s*</span>'
            rf'.*?<strong[^>]*>\s*([\d.,]+)\s*</strong>',
            document,
            re.DOTALL,
        )
        if not match:
            raise ValueError(f"BCV response does not contain {currency}")
        rates[currency] = _parse_decimal(match.group(1))
    return date.fromisoformat(date_match.group(1)), rates


def _download_bcv_html() -> str:
    # curl uses the operating system trust store, which handles BCV's current certificate
    # chain more consistently than the Python/OpenSSL bundle without disabling TLS checks.
    result = subprocess.run(
        [
            "/usr/bin/curl", "--fail", "--silent", "--show-error", "--location",
            "--max-time", "30", BCV_URL,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


class ExchangeRateService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def latest(self) -> list[ExchangeRate]:
        rows = await self.db.execute(
            select(ExchangeRate).order_by(ExchangeRate.currency, ExchangeRate.value_date.desc())
        )
        latest: dict[str, ExchangeRate] = {}
        for row in rows.scalars():
            latest.setdefault(row.currency, row)
        return list(latest.values())

    async def refresh(self) -> list[ExchangeRate]:
        document = await asyncio.to_thread(_download_bcv_html)
        value_date, rates = parse_bcv_html(document)
        if any(rate <= 0 or rate > 1_000_000_000 for rate in rates.values()):
            raise ValueError("BCV response contains an implausible exchange rate")
        for currency, rate in rates.items():
            statement = insert(ExchangeRate).values(
                currency=currency, rate_ves=rate, value_date=value_date, source=BCV_URL
            )
            statement = statement.on_conflict_do_update(
                constraint="uq_exchange_rate_currency_date",
                set_={"rate_ves": statement.excluded.rate_ves, "fetched_at": func.now()},
            )
            await self.db.execute(statement)
        await self.db.commit()
        return await self.latest()
