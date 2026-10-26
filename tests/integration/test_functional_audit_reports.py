"""The audit's canonical holding reports must work against PostgreSQL enums."""

from datetime import date

import pytest

from app.modules.filiales.models import Filial
from app.modules.holdings.control import HoldingControlService


@pytest.mark.asyncio
async def test_network_reports_share_period_and_workshop_stages(billing_db):
    ctx = billing_db
    async with ctx.sessions() as db:
        filial = await db.get(Filial, ctx.filial_id)
        service = HoldingControlService(db)
        start, end = date.today().replace(day=1), date.today()
        reports = {}
        for section in (
            "tesoreria",
            "comercial",
            "inventario",
            "taller",
            "contabilidad",
            "actividad",
        ):
            reports[section] = await service.report(filial.holding_id, section, start, end)
            assert reports[section].date_from == start
            assert reports[section].date_to == end
        commercial = next(
            table for table in reports["comercial"].tables if table.title == "Resultados por filial"
        )
        accounting = next(
            metric
            for metric in reports["contabilidad"].metrics
            if metric.label == "Ventas netas operativas"
        )
        assert sum(row["sales"] for row in commercial.rows) == accounting.value
        workshop = next(
            table for table in reports["taller"].tables if table.title == "Órdenes por fase"
        )
        assert workshop.rows[0]["phase"] == "por_facturar"
