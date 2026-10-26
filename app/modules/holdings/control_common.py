"""Shared types and formatting for holding reports."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from app.modules.holdings.control_schemas import ControlReport, Metric, ReportTable

TZ = ZoneInfo("America/Caracas")


def D(value):
    return Decimal(str(value or 0))


def value(item):
    return item.value if hasattr(item, "value") else item


def aware(dt):
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def table(title, columns, rows, note=""):
    return ReportTable(
        title=title,
        columns=[
            dict(key=c[0], label=c[1], format=c[2] if len(c) > 2 else "text") for c in columns
        ],
        rows=rows,
        note=note,
    )


@dataclass
class ReportContext:
    report: ControlReport
    holding_id: Any
    filial_id: Any
    selected: list
    ids: list
    names: dict
    records: list
    start: Any
    end: Any
    lower: datetime
    upper: datetime

    def metric(self, label, v, fmt="number", currency="usd", note=""):
        self.report.metrics.append(
            Metric(
                label=label,
                value=float(v) if isinstance(v, Decimal) else v,
                format=fmt,
                currency=currency,
                note=note,
            )
        )
