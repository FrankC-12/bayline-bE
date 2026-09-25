"""Warranty policy catalog (Post Venta) — a named, reusable policy an
advisor selects on an ODS (one for labor, one for parts) instead of the
old implicit filial-wide LaborSettings term. Never hard-deleted, only
deactivated; its links to Temparios/Parts are purely informational."""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

from app.core.database import Base
from app.core.exceptions import BadRequestError
from app.modules.parts.models import Part, PartCategory
from app.modules.post_ventas.enums import (
    TemparioCategory,
    WarrantyPolicyAppliesTo,
    WarrantyPolicyCoveredBy,
    WarrantyPolicyScope,
    WarrantyPolicyStatus,
)
from app.modules.post_ventas.models import Tempario
from app.modules.post_ventas.schemas import WarrantyPolicyCreate, WarrantyPolicyUpdate
from app.modules.post_ventas.service import PostVentasService


@pytest.fixture
def catalog():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        other_filial_id = uuid.uuid4()

        tempario = Tempario(
            filial_id=filial_id,
            category=TemparioCategory.MANTENIMIENTO_PREVENTIVO,
            sequence_number=1,
            name="Cambio de aceite",
            estimated_hours=1,
        )
        category = PartCategory(holding_id=uuid.uuid4(), name="Filtros")
        session.add_all([tempario, category])
        session.commit()

        part = Part(
            filial_id=filial_id, code="P-1", name="Filtro de aceite", category_id=category.id,
            price=10, stock_quantity=0,
        )
        foreign_tempario = Tempario(
            filial_id=other_filial_id,
            category=TemparioCategory.MANTENIMIENTO_PREVENTIVO,
            sequence_number=1,
            name="De otra filial",
            estimated_hours=1,
        )
        session.add_all([part, foreign_tempario])
        session.commit()
        yield session, filial_id, tempario, part, foreign_tempario
    engine.dispose()


def _base_payload(filial_id, **overrides):
    data = dict(
        filial_id=filial_id,
        name="Estándar de taller",
        applies_to=WarrantyPolicyAppliesTo.MANO_DE_OBRA,
        covered_by=WarrantyPolicyCoveredBy.LA_CASA,
        scope=WarrantyPolicyScope.PIEZA_MAS_INSTALACION,
        duration_days=90,
        duration_km=5000,
    )
    data.update(overrides)
    return WarrantyPolicyCreate(**data)


@pytest.mark.asyncio
async def test_create_policy_with_days_and_km(catalog):
    session, filial_id, _tempario, _part, _foreign = catalog
    service = PostVentasService(AsyncAdapter(session))

    policy = await service.create_warranty_policy(_base_payload(filial_id))

    assert policy.name == "Estándar de taller"
    assert policy.no_expiration is False
    assert policy.duration_days == 90
    assert policy.duration_km == 5000
    assert policy.status == WarrantyPolicyStatus.ACTIVA


@pytest.mark.asyncio
async def test_create_policy_without_expiration(catalog):
    session, filial_id, _tempario, _part, _foreign = catalog
    service = PostVentasService(AsyncAdapter(session))

    policy = await service.create_warranty_policy(
        _base_payload(filial_id, name="Campaña recall", no_expiration=True, duration_days=None, duration_km=None)
    )

    assert policy.no_expiration is True
    assert policy.duration_days is None
    assert policy.duration_km is None


def test_no_expiration_rejects_a_duration():
    with pytest.raises(ValidationError):
        _base_payload(uuid.uuid4(), no_expiration=True)


def test_with_expiration_requires_a_duration():
    with pytest.raises(ValidationError):
        _base_payload(uuid.uuid4(), duration_days=None, duration_km=None)


@pytest.mark.asyncio
async def test_create_policy_links_temparios_and_parts(catalog):
    session, filial_id, tempario, part, _foreign = catalog
    service = PostVentasService(AsyncAdapter(session))

    policy = await service.create_warranty_policy(
        _base_payload(
            filial_id,
            applies_to=WarrantyPolicyAppliesTo.AMBAS,
            tempario_ids=[tempario.id],
            part_ids=[part.id],
        )
    )

    assert len(policy.temparios) == 1
    assert policy.temparios[0].tempario_code == tempario.code
    assert len(policy.parts) == 1
    assert policy.parts[0].part_code == "P-1"


@pytest.mark.asyncio
async def test_create_policy_rejects_tempario_from_another_filial(catalog):
    session, filial_id, _tempario, _part, foreign_tempario = catalog
    service = PostVentasService(AsyncAdapter(session))

    with pytest.raises(BadRequestError):
        await service.create_warranty_policy(_base_payload(filial_id, tempario_ids=[foreign_tempario.id]))


@pytest.mark.asyncio
async def test_update_replaces_linked_temparios_and_parts(catalog):
    session, filial_id, tempario, part, _foreign = catalog
    service = PostVentasService(AsyncAdapter(session))
    policy = await service.create_warranty_policy(
        _base_payload(filial_id, applies_to=WarrantyPolicyAppliesTo.AMBAS, tempario_ids=[tempario.id])
    )
    assert len(policy.temparios) == 1
    assert len(policy.parts) == 0

    updated = await service.update_warranty_policy(
        policy.id, WarrantyPolicyUpdate(tempario_ids=[], part_ids=[part.id])
    )

    assert updated.temparios == []
    assert len(updated.parts) == 1
    assert updated.parts[0].part_id == part.id


@pytest.mark.asyncio
async def test_update_can_deactivate_without_deleting(catalog):
    session, filial_id, _tempario, _part, _foreign = catalog
    service = PostVentasService(AsyncAdapter(session))
    policy = await service.create_warranty_policy(_base_payload(filial_id))

    updated = await service.update_warranty_policy(
        policy.id, WarrantyPolicyUpdate(status=WarrantyPolicyStatus.INACTIVA)
    )

    assert updated.status == WarrantyPolicyStatus.INACTIVA
    # Still readable/gettable — never deleted.
    fetched = await service.get_warranty_policy(policy.id)
    assert fetched.id == policy.id


@pytest.mark.asyncio
async def test_update_switching_to_no_expiration_clears_durations(catalog):
    session, filial_id, _tempario, _part, _foreign = catalog
    service = PostVentasService(AsyncAdapter(session))
    policy = await service.create_warranty_policy(_base_payload(filial_id))

    updated = await service.update_warranty_policy(
        policy.id,
        WarrantyPolicyUpdate(no_expiration=True, clear_duration_days=True, clear_duration_km=True),
    )

    assert updated.no_expiration is True
    assert updated.duration_days is None
    assert updated.duration_km is None


@pytest.mark.asyncio
async def test_list_filters_by_status_and_applies_to(catalog):
    session, filial_id, _tempario, _part, _foreign = catalog
    service = PostVentasService(AsyncAdapter(session))
    labor = await service.create_warranty_policy(_base_payload(filial_id, name="Mano de obra"))
    parts = await service.create_warranty_policy(
        _base_payload(filial_id, name="Repuestos", applies_to=WarrantyPolicyAppliesTo.REPUESTOS)
    )
    await service.update_warranty_policy(parts.id, WarrantyPolicyUpdate(status=WarrantyPolicyStatus.INACTIVA))

    active_labor = await service.list_warranty_policies(
        filial_id, status=WarrantyPolicyStatus.ACTIVA, applies_to=WarrantyPolicyAppliesTo.MANO_DE_OBRA
    )
    assert [p.id for p in active_labor] == [labor.id]

    all_policies = await service.list_warranty_policies(filial_id)
    assert {p.id for p in all_policies} == {labor.id, parts.id}
