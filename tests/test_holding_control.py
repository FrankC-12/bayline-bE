import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa
from app.core.database import Base, get_db
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import ConflictError, NotFoundError, UnprocessableEntityError
from app.modules.administracion.enums import AccountCurrency, AccountType
from app.modules.administracion.models import Account, ExpenseEntry, IncomeEntry
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser, LoginRequest
from app.modules.filiales.models import Filial
from app.modules.holdings.control import HoldingControlService
from app.modules.holdings.control_models import HoldingRegister
from app.modules.holdings.control_schemas import RegisterCreate
from app.modules.holdings.models import Holding
from app.modules.holdings.router import router
from app.modules.roles.enums import RoleScope
from app.modules.roles.models import Role
from app.modules.users.models import User


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        holding = Holding(name="Group", slug="group")
        other = Holding(name="Other", slug="other")
        role = Role(name="Holding", slug="holding", scope=RoleScope.HOLDING)
        session.add_all([holding, other, role])
        session.flush()
        f1 = Filial(name="North", slug="north", holding_id=holding.id)
        f2 = Filial(name="South", slug="south", holding_id=holding.id)
        f3 = Filial(name="Foreign", slug="foreign", holding_id=other.id)
        actor = User(
            full_name="Director",
            email="director@example.com",
            role_id=role.id,
            holding_id=holding.id,
        )
        session.add_all([f1, f2, f3, actor])
        session.flush()
        a = Account(
            name="USD North",
            filial_id=f1.id,
            currency=AccountCurrency.USD,
            account_type=AccountType.CAJA,
            opening_balance=1000,
        )
        b = Account(
            name="USD South",
            filial_id=f2.id,
            currency=AccountCurrency.USD,
            account_type=AccountType.CORRIENTE,
            opening_balance=100,
        )
        c = Account(
            name="Foreign",
            filial_id=f3.id,
            currency=AccountCurrency.USD,
            account_type=AccountType.CAJA,
            opening_balance=99999,
        )
        bs = Account(
            name="Bs South",
            filial_id=f2.id,
            currency=AccountCurrency.BS,
            account_type=AccountType.CAJA,
            opening_balance=10000,
        )
        session.add_all([a, b, c, bs])
        session.commit()
        yield session, HoldingControlService(AsyncAdapter(session)), holding, other, actor, (
            f1,
            f2,
            f3,
        ), (a, b, c, bs)


def request(record, request_id=None):
    return RegisterCreate(
        request_id=request_id or uuid.uuid4(), entry_date=date.today(), record=record
    )


@pytest.mark.asyncio
async def test_control_reports_work_for_every_section_and_do_not_leak(env):
    session, svc, h, other, actor, fs, accounts = env
    for section in ("tesoreria", "comercial", "inventario", "taller", "contabilidad", "actividad"):
        report = await svc.report(h.id, section, date.today(), date.today())
        assert len(report.filiales) == 2
        assert "Foreign" not in report.model_dump_json()
    report = await svc.report(h.id, "tesoreria", date.today(), date.today())
    assert report.metrics[0].value == 1100
    with pytest.raises(NotFoundError):
        await svc.report(h.id, "tesoreria", date.today(), date.today(), fs[2].id)


@pytest.mark.asyncio
async def test_intercompany_transfer_posts_two_sides_once_and_preserves_consolidated_cash(env):
    session, svc, h, other, actor, fs, (a, b, c, bs) = env
    payload = request(
        dict(
            kind="funds_transfer",
            source_account_id=a.id,
            destination_account_id=b.id,
            amount="250.10",
            reference="Bank 123",
        )
    )
    row = await svc.register(h.id, actor.id, payload)
    assert (await svc.register(h.id, actor.id, payload)).id == row.id
    assert len(session.scalars(select(ExpenseEntry)).all()) == 1
    assert len(session.scalars(select(IncomeEntry)).all()) == 1
    balances = dict((a.id, b) for a, b in await svc.balances([fs[0].id, fs[1].id]))
    assert balances[a.id] == Decimal("749.90")
    assert balances[b.id] == Decimal("350.10")
    assert balances[a.id] + balances[b.id] == 1100
    changed = payload.model_copy(
        update={"record": payload.record.model_copy(update={"amount": Decimal("1")})}
    )
    with pytest.raises(ConflictError):
        await svc.register(h.id, actor.id, changed)
    for dest in (c, bs):
        with pytest.raises((NotFoundError, UnprocessableEntityError)):
            await svc.register(
                h.id,
                actor.id,
                request(
                    dict(
                        kind="funds_transfer",
                        source_account_id=a.id,
                        destination_account_id=dest.id,
                        amount="1",
                        reference="test",
                    )
                ),
            )


@pytest.mark.asyncio
async def test_cash_count_is_evidence_not_a_silent_balance_adjustment(env):
    session, svc, h, other, actor, fs, (a, b, c, bs) = env
    cashier_role = Role(name="Caja", slug="caja-test", scope=RoleScope.FILIAL)
    session.add(cashier_role)
    session.flush()
    actor.role_id = cashier_role.id
    actor.filial_id = fs[0].id
    session.commit()
    row = await svc.register(
        h.id,
        actor.id,
        request(dict(kind="cash_count", filial_id=fs[0].id, account_id=a.id, counted="995.25")),
    )
    assert row.payload["evidence"]["difference"] == "-4.75"
    assert session.scalars(select(ExpenseEntry)).all() == []
    with pytest.raises(UnprocessableEntityError):
        await svc.register(
            h.id,
            actor.id,
            request(dict(kind="cash_count", filial_id=fs[1].id, account_id=b.id, counted="100")),
        )


@pytest.mark.asyncio
async def test_tax_payment_reduces_the_obligation_and_posts_an_expense(env):
    session, svc, h, other, actor, fs, (a, b, c, bs) = env
    tax = await svc.register(
        h.id,
        actor.id,
        request(
            dict(
                kind="tax",
                filial_id=fs[0].id,
                name="IVA",
                currency="usd",
                amount="200",
                due_date=date.today(),
            )
        ),
    )
    await svc.register(
        h.id,
        actor.id,
        request(
            dict(
                kind="tax_payment",
                filial_id=fs[0].id,
                obligation_id=tax.id,
                account_id=a.id,
                amount="50",
                reference="receipt",
            )
        ),
    )
    report = await svc.report(h.id, "tesoreria", date.today(), date.today())
    payments = next(t for t in report.tables if t.title == "Calendario global de pagos")
    assert payments.rows[0]["amount"] == 150
    with pytest.raises(UnprocessableEntityError):
        await svc.register(
            h.id,
            actor.id,
            request(
                dict(
                    kind="tax_payment",
                    filial_id=fs[0].id,
                    obligation_id=tax.id,
                    account_id=a.id,
                    amount="151",
                    reference="overpay",
                )
            ),
        )


@pytest.mark.asyncio
async def test_journals_balance_and_eliminations_only_affect_the_consolidated_book(env):
    session, svc, h, other, actor, fs, accounts = env
    lines = [
        dict(account="Caja", category="asset", debit="100"),
        dict(account="Capital", category="equity", credit="100"),
    ]
    await svc.register(
        h.id,
        actor.id,
        request(dict(kind="journal", filial_id=fs[0].id, description="Opening", lines=lines)),
    )
    await svc.register(
        h.id,
        actor.id,
        request(
            dict(
                kind="journal",
                elimination=True,
                description="Elimination",
                lines=[
                    dict(account="Internal revenue", category="revenue", debit="20"),
                    dict(account="Internal cost", category="cost", credit="20"),
                ],
            )
        ),
    )
    full = await svc.report(h.id, "contabilidad", date.today(), date.today())
    local = await svc.report(h.id, "contabilidad", date.today(), date.today(), fs[0].id)
    journal = lambda r: next(t for t in r.tables if t.title == "Asientos y eliminaciones")
    assert len(journal(full).rows) == 2
    assert len(journal(local).rows) == 1
    with pytest.raises(ValueError):
        request(
            dict(
                kind="journal",
                filial_id=fs[0].id,
                description="Unbalanced",
                lines=[
                    dict(account="Cash", category="asset", debit="10"),
                    dict(account="Capital", category="equity", credit="11"),
                ],
            )
        )


@pytest.mark.asyncio
async def test_http_rejects_other_holdings_and_filial_scope(env):
    session, svc, h, other, actor, fs, accounts = env
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    app.dependency_overrides[get_db] = lambda: svc.db
    user = CurrentUser(
        user_id=actor.id,
        email=actor.email,
        role_id=actor.role_id,
        role_slug="holding",
        scope=RoleScope.HOLDING,
        holding_id=h.id,
        filial_id=None,
    )
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(f"/holdings/{h.id}/control/tesoreria")).status_code == 200
        assert (await client.get(f"/holdings/{other.id}/control/tesoreria")).status_code == 403
        assert (
            await client.post(
                f"/holdings/{other.id}/registers",
                json=request(dict(kind="target", filial_id=fs[0].id, amount="100")).model_dump(
                    mode="json"
                ),
            )
        ).status_code == 403
        user = user.model_copy(update={"scope": RoleScope.FILIAL, "filial_id": fs[0].id})
        assert (await client.get(f"/holdings/{h.id}/control/tesoreria")).status_code == 403


def test_reserved_platform_alias_and_regular_email_validation():
    from app.core.config import get_settings

    assert (
        LoginRequest(email="motrixadmin", password="secret").email
        == get_settings().platform_admin_email
    )
    with pytest.raises(ValueError):
        LoginRequest(email="randomuser", password="secret")


@pytest.mark.asyncio
async def test_cross_filial_stock_requires_approval_and_preserves_each_fifo_cost(env):
    from app.modules.parts.models import Part, PartCategory
    from app.modules.parts.enums import PartAvailability
    from app.modules.warehouse.models import Warehouse, PartLot

    session, svc, h, other, actor, (f1, f2, f3), accounts = env
    category = PartCategory(holding_id=h.id, name="Filters")
    wa = Warehouse(filial_id=f1.id, name="Origin")
    wb = Warehouse(filial_id=f2.id, name="Destination")
    session.add_all([category, wa, wb])
    session.flush()
    a = Part(
        filial_id=f1.id,
        code="F-1",
        name="Filter",
        category_id=category.id,
        price=10,
        unit="Unidad",
        stock_quantity=7,
        availability=PartAvailability.DISPONIBLE,
    )
    b = Part(
        filial_id=f2.id,
        code="F-1",
        name="Filter",
        category_id=category.id,
        price=10,
        unit="Unidad",
        stock_quantity=0,
        availability=PartAvailability.AGOTADO,
    )
    session.add_all([a, b])
    session.flush()
    from datetime import UTC, datetime, timedelta

    lots = [
        PartLot(
            filial_id=f1.id,
            warehouse_id=wa.id,
            part_id=a.id,
            quantity_received=2,
            quantity_remaining=2,
            unit_cost=5,
            received_at=datetime.now(UTC) - timedelta(days=2),
        ),
        PartLot(
            filial_id=f1.id,
            warehouse_id=wa.id,
            part_id=a.id,
            quantity_received=5,
            quantity_remaining=5,
            unit_cost=9,
            received_at=datetime.now(UTC) - timedelta(days=1),
        ),
    ]
    session.add_all(lots)
    session.commit()
    root = await svc.register(
        h.id,
        actor.id,
        request(
            dict(
                kind="stock_transfer",
                source_warehouse_id=wa.id,
                destination_warehouse_id=wb.id,
                lines=[dict(source_part_id=a.id, destination_part_id=b.id, quantity=3)],
                note="Restock south",
            )
        ),
    )

    def action(name):
        return request(
            dict(
                kind="stock_transfer_action",
                transfer_id=root.id,
                action=name,
                note="Physical confirmation",
            )
        )

    with pytest.raises(UnprocessableEntityError):
        await svc.register(h.id, actor.id, action("dispatch"))
    await svc.register(h.id, actor.id, action("approve"))
    await svc.register(h.id, actor.id, action("dispatch"))
    assert a.stock_quantity == 4
    assert b.stock_quantity == 0
    received = action("receive")
    row = await svc.register(h.id, actor.id, received)
    assert (await svc.register(h.id, actor.id, received)).id == row.id
    dest = session.scalars(select(PartLot).where(PartLot.part_id == b.id)).all()
    assert sorted((x.quantity_received, x.unit_cost) for x in dest) == [
        (1, Decimal("9")),
        (2, Decimal("5")),
    ]
    assert {x.origin_lot_id for x in dest} == {x.id for x in lots}
    assert b.stock_quantity == 3
    with pytest.raises(UnprocessableEntityError):
        await svc.register(h.id, actor.id, action("receive"))


def test_audit_commits_with_changes_and_rolls_back_with_them(env):
    from app.core.audit_logging import request_context
    from app.modules.holdings.audit import register_holding_audit
    from app.modules.holdings.control_models import HoldingAuditEvent

    session, svc, h, other, actor, fs, accounts = env
    register_holding_audit()
    token = request_context.set({"user_id": str(actor.id), "holding_id": str(h.id)})
    try:
        accounts[0].name = "Counted cash"
        session.commit()
        event = session.scalars(select(HoldingAuditEvent)).one()
        assert event.holding_id == h.id and event.filial_id == fs[0].id
        assert event.user_id == actor.id and "name" in event.changed_fields
        accounts[0].name = "Uncommitted change"
        session.flush()
        session.rollback()
        assert len(session.scalars(select(HoldingAuditEvent)).all()) == 1
    finally:
        request_context.reset(token)


@pytest.mark.asyncio
async def test_platform_bootstrap_is_idempotent_and_does_not_promote_tenant_accounts(
    env, monkeypatch
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from app.scripts import seed_platform_user as bootstrap
    from app.core.security import verify_password

    session, svc, h, other, actor, fs, accounts = env

    @asynccontextmanager
    async def factory():
        yield svc.db

    settings = SimpleNamespace(
        platform_admin_email="owner@motrix.example", platform_admin_password="Test-password-2026"
    )
    monkeypatch.setattr(bootstrap, "AsyncSessionLocal", factory)
    monkeypatch.setattr(bootstrap, "get_settings", lambda: settings)
    await bootstrap.seed_platform_user()
    admin = session.scalars(select(User).where(User.email == settings.platform_admin_email)).one()
    assert admin.full_name == "motrixadmin"
    assert session.get(Role, admin.role_id).scope == RoleScope.PLATFORM
    assert verify_password("Test-password-2026", admin.password_hash)
    original = admin.password_hash
    settings.platform_admin_password = "Different-password-2026"
    await bootstrap.seed_platform_user()
    assert admin.password_hash == original
    settings.platform_admin_email = actor.email
    with pytest.raises(RuntimeError, match="sin alcance de plataforma"):
        await bootstrap.seed_platform_user()


@pytest.mark.asyncio
async def test_opportunity_stage_updates_keep_one_card_and_the_full_history(env):
    session, svc, h, other, actor, fs, accounts = env
    root = await svc.register(
        h.id,
        actor.id,
        request(
            dict(
                kind="opportunity",
                filial_id=fs[0].id,
                client="Buyer",
                description="Fleet quote",
                amount="1000",
                stage="contacto",
            )
        ),
    )
    second = await svc.register(
        h.id,
        actor.id,
        request(
            dict(
                kind="opportunity",
                filial_id=fs[0].id,
                client="Buyer",
                description="Fleet quote",
                amount="1000",
                stage="cotizacion",
                opportunity_id=root.id,
            )
        ),
    )
    await svc.register(
        h.id,
        actor.id,
        request(
            dict(
                kind="opportunity",
                filial_id=fs[0].id,
                client="Buyer",
                description="Fleet quote",
                amount="1000",
                stage="negociacion",
                opportunity_id=second.id,
            )
        ),
    )
    report = await svc.report(h.id, "comercial", date.today(), date.today())
    rows = next(t.rows for t in report.tables if t.title == "Embudo comercial")
    assert len(rows) == 1 and rows[0]["stage"] == "negociacion"
    assert (
        len(
            session.scalars(
                select(HoldingRegister).where(HoldingRegister.kind == "opportunity")
            ).all()
        )
        == 3
    )


@pytest.mark.asyncio
async def test_delivery_is_distinct_from_sold_and_cannot_be_recorded_twice(env):
    from app.modules.concesionario.models import DealershipVehicle, VehicleSale
    from app.modules.concesionario.enums import VehicleCondition, VehicleStatus, SaleType

    session, svc, h, other, actor, fs, accounts = env
    vehicle = DealershipVehicle(
        filial_id=fs[0].id,
        brand="Toyota",
        model="Hilux",
        year=2026,
        vin="12345678901234567",
        sku="V1",
        price_cash=100,
        price_financed=110,
        status=VehicleStatus.VENDIDO,
        condition=VehicleCondition.NUEVO,
    )
    session.add(vehicle)
    session.flush()
    session.add(
        VehicleSale(
            filial_id=fs[0].id,
            vehicle_id=vehicle.id,
            sequence_number=1,
            client_name="Buyer",
            sale_type=SaleType.CONTADO,
            final_price=100,
        )
    )
    session.commit()
    payload = request(
        dict(
            kind="vehicle_delivery",
            filial_id=fs[0].id,
            vehicle_id=vehicle.id,
            note="Client signed receipt",
        )
    )
    await svc.register(h.id, actor.id, payload)
    report = await svc.report(h.id, "inventario", date.today(), date.today())
    assert (
        next(t.rows for t in report.tables if t.title == "Vehículos por sucursal")[0]["status"]
        == "entregado"
    )
    assert next(m.value for m in report.metrics if m.label == "Vehículos en inventario") == 0
    with pytest.raises(ConflictError):
        await svc.register(h.id, actor.id, payload.model_copy(update={"request_id": uuid.uuid4()}))


@pytest.mark.asyncio
async def test_risk_policy_monitors_real_discount_vs_normal_pvp(env):
    from app.modules.parts.models import PartSale
    from app.modules.parts.enums import PartSaleStatus

    session, svc, h, other, actor, fs, accounts = env
    session.add(
        PartSale(
            filial_id=fs[0].id,
            sequence_number=1,
            client_name="Buyer",
            discount_label="Precio de costo",
            status=PartSaleStatus.COMPLETADO,
        )
    )
    session.commit()
    await svc.register(
        h.id,
        actor.id,
        request(dict(kind="risk_policy", max_discount_percentage="15", workshop_warning_hours=24)),
    )
    report = await svc.report(h.id, "actividad", date.today(), date.today(), fs[0].id)
    rows = next(
        t.rows
        for t in report.tables
        if t.title == "Alertas: descuentos sobre el límite del holding"
    )
    assert len(rows) == 1 and rows[0]["discount"] == 23.08


def test_installation_role_seed_contains_only_known_modules():
    from app.scripts.seed_roles import ALL_MODULES,ROLES_SEED
    assert {"platform","holding"}.issubset({role["slug"] for role in ROLES_SEED})
    assert all(permission["module_id"] in ALL_MODULES for role in ROLES_SEED for permission in role["permissions"])


@pytest.mark.asyncio
async def test_same_day_commercial_sales_use_complete_caracas_days_and_percentage_margins(env):
    from datetime import UTC,datetime
    from app.modules.concesionario.models import DealershipVehicle,VehicleSale
    from app.modules.concesionario.enums import VehicleCondition,VehicleStatus,SaleType
    session,svc,h,other,actor,fs,accounts=env
    for index,(hour,day,price) in enumerate([(3,15,999),(12,15,100),(2,16,50),(4,16,777)]):
        v=DealershipVehicle(filial_id=fs[0].id,brand="Toyota",model="Hilux",year=2026,vin=str(index).zfill(17),sku=f"D{index}",price_cash=price,price_financed=price,status=VehicleStatus.VENDIDO,condition=VehicleCondition.NUEVO,cost_price=0)
        session.add(v);session.flush()
        session.add(VehicleSale(filial_id=fs[0].id,vehicle_id=v.id,sequence_number=index+1,client_name="Buyer",sale_type=SaleType.CONTADO,final_price=price,created_at=datetime(2026,10,day,hour,tzinfo=UTC)))
    session.commit()
    report=await svc.report(h.id,"comercial",date(2026,10,15),date(2026,10,15))
    row=next(t.rows for t in report.tables if t.title=="Resultados por filial")[0]
    assert row["vehicles"]==2
    assert row["sales"]==150
    assert row["ticket"]==75
    assert row["margin"]==100


@pytest.mark.asyncio
async def test_below_cost_alert_includes_legacy_sales_and_respects_scope_and_period(env):
    from datetime import UTC, datetime, timedelta
    from app.modules.concesionario.models import DealershipVehicle, VehicleSale
    from app.modules.concesionario.enums import VehicleCondition, VehicleStatus, SaleType

    session, svc, holding, other, actor, filiales, accounts = env
    now = datetime.now(UTC)
    cases = [
        (filiales[0], 3123, 30000, False, now),
        (filiales[0], 5000, 6000, True, now),
        (filiales[0], 30000, 30000, False, now),
        (filiales[0], 32000, 30000, False, now),
        (filiales[0], 1000, None, False, now),
        (filiales[1], 1000, 30000, False, now),
        (filiales[2], 1000, 30000, False, now),
        (filiales[0], 1000, 30000, False, now - timedelta(days=40)),
    ]
    for index, (filial, price, cost, authorized, created_at) in enumerate(cases):
        vehicle = DealershipVehicle(
            filial_id=filial.id,
            brand="Toyota",
            model="Corolla",
            year=2026,
            vin=str(index).zfill(17),
            sku=f"RISK-{index}",
            price_cash=45000,
            price_financed=45000,
            cost_price=cost,
            status=VehicleStatus.VENDIDO,
            condition=VehicleCondition.NUEVO,
        )
        session.add(vehicle)
        session.flush()
        session.add(
            VehicleSale(
                filial_id=filial.id,
                vehicle_id=vehicle.id,
                sequence_number=index + 1,
                client_name="Cliente",
                sale_type=SaleType.CONTADO,
                final_price=price,
                below_cost_override=authorized,
                authorized_by_user_id=actor.id if authorized else None,
                authorized_at=now if authorized else None,
                created_at=created_at,
            )
        )
    session.commit()
    # Use a Caracas business date, including executions shortly after UTC midnight.
    from zoneinfo import ZoneInfo

    today = now.astimezone(ZoneInfo("America/Caracas")).date()
    report = await svc.report(holding.id, "actividad", today, today, filiales[0].id)
    rows = next(t.rows for t in report.tables if t.title == "Alertas: ventas bajo costo")
    by_code = {row["code"]: row for row in rows}
    assert set(by_code) == {"CV-0001", "CV-0002"}
    assert by_code["CV-0001"]["loss"] == -26877
    assert by_code["CV-0001"]["cost"] == 30000
    assert by_code["CV-0001"]["authorization"] == "Sin autorización registrada: revisar"
    assert by_code["CV-0002"]["authorization"] == "Autorización registrada"
