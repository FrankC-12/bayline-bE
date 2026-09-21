"""The app operates on a single business timezone regardless of where the
server process happens to run: every timestamp column is stored as a UTC
instant, but "today"/"this day" for anything bucketed by calendar day (an
invoice's billing day, an income entry's date) always means Venezuela's
calendar day, not the server's."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

VENEZUELA_TZ = ZoneInfo("America/Caracas")


def venezuela_now() -> datetime:
    return datetime.now(VENEZUELA_TZ)


def venezuela_today() -> date:
    return venezuela_now().date()


def aging_bucket(days_outstanding: int) -> str:
    """Standard receivables aging buckets, from an issuance date to today."""
    if days_outstanding <= 30:
        return "0-30"
    if days_outstanding <= 60:
        return "31-60"
    if days_outstanding <= 90:
        return "61-90"
    return "90+"
