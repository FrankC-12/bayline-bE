"""Onboarding validates all rows, scope and review before any insert."""

import csv
import io
import uuid

import pytest
from sqlalchemy import func, select
from test_tempario_pricing_matches_ods import env

from app.core.exceptions import BadRequestError, ConflictError
from app.modules.ajustes.bulk import HEADERS, CatalogBulkService, CatalogKind, number, template_csv
from app.modules.filiales.models import Filial
from app.modules.parts.models import PartCategory
from app.modules.post_ventas.models import Tempario
from app.modules.vehicle_catalog.models import VehicleBrand, VehicleModel

__all__ = ["env"]


@pytest.mark.parametrize("kind", list(CatalogKind))
def test_download_templates_have_only_real_headers_and_no_sample_records(kind):
    rows = list(csv.reader(io.StringIO(template_csv(kind).lstrip("\ufeff")), delimiter=";"))
    assert rows == [HEADERS[kind]]


@pytest.mark.parametrize(
    "raw,expected", [("12.700,00", 12700), ("1.000", 1000), ("0.500", 0.5), ("12700.50", 12700.5)]
)
def test_bulk_numeric_separators_preserve_amounts(raw, expected):
    assert number(raw) == expected


def test_ambiguous_foreign_separators_are_rejected_instead_of_changing_value():
    with pytest.raises(ValueError):
        number("1,234.56")


@pytest.mark.asyncio
async def test_invalid_rows_never_partially_import_and_scope_cannot_be_injected(env):
    catalog, _, order, _ = env
    service = CatalogBulkService(catalog.db)
    actor = uuid.uuid4()
    rows = [
        {"nombre": "Una categoría", "activo": "si"},
        {"nombre": "Otra", "holding_id": str(uuid.uuid4())},
    ]
    report = await service.prepare(CatalogKind.CATEGORIES, order.filial_id, actor, rows)
    assert report["valid"] == 1 and report["errors"][0]["row"] == 3
    with pytest.raises(BadRequestError):
        await service.prepare(
            CatalogKind.CATEGORIES,
            order.filial_id,
            actor,
            rows,
            commit=True,
            review_hash=report["review_hash"],
        )
    assert catalog.db.session.scalar(select(func.count()).select_from(PartCategory)) == 0


@pytest.mark.asyncio
async def test_review_tampering_and_repeat_import_are_rejected(env):
    catalog, _, order, _ = env
    service = CatalogBulkService(catalog.db)
    actor = uuid.uuid4()
    rows = [{"nombre": "Marca nueva", "activo": "si"}]
    report = await service.prepare(CatalogKind.BRANDS, order.filial_id, actor, rows)
    with pytest.raises(ConflictError):
        await service.prepare(
            CatalogKind.BRANDS,
            order.filial_id,
            actor,
            [{"nombre": "Cambio"}],
            commit=True,
            review_hash=report["review_hash"],
        )
    imported = await service.prepare(
        CatalogKind.BRANDS,
        order.filial_id,
        actor,
        rows,
        commit=True,
        review_hash=report["review_hash"],
    )
    assert imported["imported"] == 1
    duplicate = await service.prepare(CatalogKind.BRANDS, order.filial_id, actor, rows)
    assert duplicate["errors"]
    assert catalog.db.session.scalar(select(func.count()).select_from(VehicleBrand)) == 1


@pytest.mark.asyncio
async def test_model_requires_existing_brand_and_services_validate_real_part_codes(env):
    catalog, _, order, _ = env
    session = catalog.db.session
    filial = await catalog.db.get(Filial, order.filial_id)
    brand = VehicleBrand(holding_id=filial.holding_id, name="Toyota")
    session.add(brand)
    session.commit()
    service = CatalogBulkService(catalog.db)
    actor = uuid.uuid4()
    models = [{"marca": "Toyota", "nombre": "Corolla", "tipo_vehiculo": "Sedán"}]
    preview = await service.prepare(CatalogKind.MODELS, order.filial_id, actor, models)
    assert preview["errors"] == []
    await service.prepare(
        CatalogKind.MODELS,
        order.filial_id,
        actor,
        models,
        commit=True,
        review_hash=preview["review_hash"],
    )
    assert session.scalar(select(func.count()).select_from(VehicleModel)) == 1
    rows = [
        {
            "categoria": "motor",
            "nombre": "Trabajo importado",
            "horas": "0,50",
            "garantia": "custom",
            "dias": "90",
            "compatibilidad": "Toyota:Corolla",
            "repuestos": "P-1:2:12,70",
        }
    ]
    preview = await service.prepare(CatalogKind.SERVICES, order.filial_id, actor, rows)
    assert preview["errors"] == []
    await service.prepare(
        CatalogKind.SERVICES,
        order.filial_id,
        actor,
        rows,
        commit=True,
        review_hash=preview["review_hash"],
    )
    created = session.scalar(select(Tempario).where(Tempario.name == "Trabajo importado"))
    assert float(created.estimated_hours) == 0.5 and created.parts[0].quantity == 2
    assert float(created.parts[0].unit_cost) == 12.7
    assert created.sequence_number == 501
