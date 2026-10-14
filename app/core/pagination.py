from datetime import date, datetime, time, timedelta
from math import ceil
from typing import Generic, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.exceptions import BadRequestError

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


async def page_query(db, query, page, page_size):
    total = (
        await db.execute(select(func.count()).select_from(query.order_by(None).subquery()))
    ).scalar_one()
    pages = max(1, ceil(total / page_size))
    page = min(page, pages)
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    return result, {"total": total, "page": page, "page_size": page_size, "total_pages": pages}


def date_range(query, column, date_from, date_to):
    if date_from and date_to and date_from > date_to:
        raise BadRequestError("La fecha inicial no puede ser posterior a la final.")
    timezone = ZoneInfo("America/Caracas")
    if date_from:
        query = query.where(column >= datetime.combine(date_from, time.min, timezone))
    if date_to and date_to < date.max:
        query = query.where(
            column < datetime.combine(date_to + timedelta(days=1), time.min, timezone)
        )
    return query
