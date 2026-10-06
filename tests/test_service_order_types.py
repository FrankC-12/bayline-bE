"""'Tipo de ODS' used to be a fixed Python/Postgres enum — it's now a
filial-scoped, admin-manageable catalog (ServiceOrderTypeCatalog). Six
system rows are seeded per filial (regular/mpt/retrabajo/garantia_fabrica/
comeback/campana); an admin can add more from Postventas, but those never
carry claim_type/is_selectable — they always behave like "regular". Listing
is gated by asesor-servicios VER (anyone creating an ODS needs the picker);
creating/editing the catalog is gated by post-ventas EDITAR."""

import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter, seed_order_types_sync

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.auth.exceptions import InsufficientPermissionsError
from app.modules.auth.schemas import CurrentUser
from app.modules.filiales.models import Filial
from app.modules.filiales.schemas import FilialCreate
from app.modules.filiales.service import FilialService
from app.modules.holdings.models import Holding
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.service_orders.exceptions import ServiceOrderTypeNotFoundError
from app.modules.service_orders.models import ServiceOrderTypeCatalog
from app.modules.service_orders.router import _ensure_access, _ensure_order_types_access
from app.modules.service_orders.schemas import ServiceOrderTypeCreate, ServiceOrderTypeUpdate
from app.modules.service_orders.service import ServiceOrderService


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        session.commit()
        yield ServiceOrderService(AsyncAdapter(session)), session, filial_id


@pytest.fixture
def make_user():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False, autoflush=False)
    filial_id = uuid.uuid4()
    session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
    session.commit()

    def _make(module_id: str, access: AccessLevel):
        role = Role(id=uuid.uuid4(), name="Rol de prueba", slug=f"rol-{uuid.uuid4()}", scope=RoleScope.FILIAL)
        session.add(role)
        session.add(RoleModulePermission(role_id=role.id, module_id=module_id, access=access))
        session.commit()
        user = CurrentUser(
            user_id=uuid.uuid4(), email="user@test.com", role_id=role.id, role_slug=role.slug,
            scope=RoleScope.FILIAL, holding_id=None, filial_id=filial_id,
        )
        return AsyncAdapter(session), user, filial_id

    yield _make
    session.close()


@pytest.mark.asyncio
async def test_creating_a_filial_seeds_the_six_system_order_types(env):
    _service, session, _filial_id = env
    holding = Holding(name="Grupo", slug="grupo")
    session.add(holding)
    session.commit()

    filial_service = FilialService(AsyncAdapter(session))
    filial = await filial_service.create_filial(
        FilialCreate(holding_id=holding.id, name="Sucursal Este", slug="sucursal-este")
    )

    rows = session.execute(
        select(ServiceOrderTypeCatalog).where(ServiceOrderTypeCatalog.filial_id == filial.id)
    ).scalars().all()
    codes = {r.code for r in rows}
    assert codes == {"regular", "mpt", "retrabajo", "garantia_fabrica", "comeback", "campana"}
    assert all(r.is_system for r in rows)
    regular = next(r for r in rows if r.code == "regular")
    assert regular.claim_type is None
    assert regular.is_selectable is True
    retrabajo = next(r for r in rows if r.code == "retrabajo")
    assert retrabajo.is_selectable is False
    garantia = next(r for r in rows if r.code == "garantia_fabrica")
    assert garantia.claim_type is not None


@pytest.mark.asyncio
async def test_create_order_type_derives_a_slug_code_from_the_name(env):
    service, _session, filial_id = env
    created = await service.create_order_type(
        filial_id, ServiceOrderTypeCreate(filial_id=filial_id, name="Revisión Pre-Entrega", description="PDI")
    )

    assert created.code == "revision_pre_entrega"
    assert created.is_system is False
    assert created.claim_type is None
    assert created.is_selectable is True
    assert created.is_active is True


@pytest.mark.asyncio
async def test_create_order_type_disambiguates_a_colliding_code(env):
    service, _session, filial_id = env
    first = await service.create_order_type(filial_id, ServiceOrderTypeCreate(filial_id=filial_id, name="Campaña"))
    second = await service.create_order_type(filial_id, ServiceOrderTypeCreate(filial_id=filial_id, name="Campaña"))

    assert first.code != second.code
    assert second.code == f"{first.code}_2"


@pytest.mark.asyncio
async def test_update_order_type_can_rename_and_deactivate_even_a_system_row(env):
    service, session, filial_id = env
    seed_order_types_sync(session, filial_id)
    regular = session.execute(
        select(ServiceOrderTypeCatalog).where(
            ServiceOrderTypeCatalog.filial_id == filial_id, ServiceOrderTypeCatalog.code == "regular"
        )
    ).scalar_one()

    updated = await service.update_order_type(
        regular.id, ServiceOrderTypeUpdate(name="Mantenimiento general", is_active=False)
    )

    assert updated.name == "Mantenimiento general"
    assert updated.is_active is False
    assert updated.code == "regular"  # never editable
    assert updated.is_system is True  # never editable


@pytest.mark.asyncio
async def test_update_order_type_for_an_unknown_id_raises(env):
    service, _session, _filial_id = env
    with pytest.raises(ServiceOrderTypeNotFoundError):
        await service.update_order_type(uuid.uuid4(), ServiceOrderTypeUpdate(name="x"))


@pytest.mark.asyncio
async def test_create_order_rejects_an_inactive_or_foreign_order_type(env):
    service, session, filial_id = env
    seed_order_types_sync(session, filial_id)
    other_filial_id = uuid.uuid4()
    session.add(Filial(id=other_filial_id, holding_id=uuid.uuid4(), name="Otro", slug="otro"))
    seed_order_types_sync(session, other_filial_id)
    foreign_type = session.execute(
        select(ServiceOrderTypeCatalog).where(
            ServiceOrderTypeCatalog.filial_id == other_filial_id, ServiceOrderTypeCatalog.code == "regular"
        )
    ).scalar_one()

    from app.modules.service_orders.exceptions import ServiceOrderTypeInvalidError
    from app.modules.service_orders.schemas import ServiceOrderCreate

    payload = ServiceOrderCreate(
        filial_id=filial_id, vehicle_id=uuid.uuid4(), order_type_id=foreign_type.id,
        customer_reason="Motivo", advisor_user_id=uuid.uuid4(),
        promised_at="2026-09-10T09:00:00", scheduled_at="2026-09-15T10:00:00+00:00",
    )
    with pytest.raises(ServiceOrderTypeInvalidError):
        await service.create_order(payload)


# Permission gating — list (VER on asesor-servicios) vs. create/update (EDITAR on post-ventas).


@pytest.mark.asyncio
async def test_asesor_servicios_can_list_order_types(make_user):
    db, user, filial_id = make_user("asesor-servicios", AccessLevel.VER)
    await _ensure_access(user, filial_id, db)


@pytest.mark.asyncio
async def test_asesor_servicios_alone_cannot_create_an_order_type(make_user):
    db, user, filial_id = make_user("asesor-servicios", AccessLevel.EDITAR)
    with pytest.raises(InsufficientPermissionsError):
        await _ensure_order_types_access(user, filial_id, db, AccessLevel.EDITAR)


@pytest.mark.asyncio
async def test_post_ventas_editar_can_create_an_order_type(make_user):
    db, user, filial_id = make_user("post-ventas", AccessLevel.EDITAR)
    await _ensure_order_types_access(user, filial_id, db, AccessLevel.EDITAR)


@pytest.mark.asyncio
async def test_post_ventas_ver_cannot_create_an_order_type(make_user):
    db, user, filial_id = make_user("post-ventas", AccessLevel.VER)
    with pytest.raises(InsufficientPermissionsError):
        await _ensure_order_types_access(user, filial_id, db, AccessLevel.EDITAR)
