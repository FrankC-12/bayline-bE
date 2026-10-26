"""Holding writes and retry safety against real PostgreSQL transactions."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.modules.administracion.enums import AccountCurrency, AccountType
from app.modules.administracion.models import Account, ExpenseEntry, IncomeEntry
from app.modules.filiales.models import Filial
from app.modules.holdings.control import HoldingControlService
from app.modules.holdings.control_models import HoldingRegister
from app.modules.holdings.control_schemas import RegisterCreate
from app.modules.roles.enums import RoleScope
from app.modules.roles.models import Role
from app.modules.service_orders.billing import billing_day
from app.modules.users.models import User


@pytest.mark.asyncio
async def test_holding_control_parallel_retry_posts_only_one_pair(billing_db):
    ctx = billing_db
    async with ctx.sessions() as db:
        origin = await db.get(Filial, ctx.filial_id)
        holding_id = origin.holding_id
        role = (await db.execute(select(Role).where(Role.slug == "holding"))).scalar_one_or_none()
        if role is None:
            role = Role(name="Holding", slug="holding", scope=RoleScope.HOLDING)
            db.add(role)
            await db.flush()
        actor = User(
            full_name="Holding integration",
            email=f"{uuid.uuid4().hex}@example.com",
            role_id=role.id,
            holding_id=holding_id,
        )
        destination = Filial(name="Destination", slug=uuid.uuid4().hex, holding_id=holding_id)
        db.add_all([actor, destination])
        await db.flush()
        account = Account(
            name="Destination",
            filial_id=destination.id,
            currency=AccountCurrency.USD,
            account_type=AccountType.CORRIENTE,
            opening_balance=100,
        )
        db.add(account)
        source = await db.get(Account, ctx.usd_account_id)
        source.opening_balance = 1000
        await db.commit()
        actor_id = actor.id
        dest_id = account.id
    payload = RegisterCreate(
        request_id=uuid.uuid4(),
        entry_date=billing_day(),
        record=dict(
            kind="funds_transfer",
            source_account_id=ctx.usd_account_id,
            destination_account_id=dest_id,
            amount="150.25",
            reference="PG parallel retry",
        ),
    )

    async def register():
        async with ctx.sessions() as db:
            return (await HoldingControlService(db).register(holding_id, actor_id, payload)).id

    first, second = await asyncio.gather(register(), register())
    assert first == second
    async with ctx.sessions() as db:
        assert (
            await db.execute(
                select(func.count())
                .select_from(HoldingRegister)
                .where(HoldingRegister.holding_id == holding_id)
            )
        ).scalar() == 1
        assert (
            await db.execute(
                select(func.count())
                .select_from(ExpenseEntry)
                .where(ExpenseEntry.account_id == ctx.usd_account_id)
            )
        ).scalar() == 1
        assert (
            await db.execute(
                select(func.count())
                .select_from(IncomeEntry)
                .where(IncomeEntry.account_id == dest_id)
            )
        ).scalar() == 1
        balances = dict(
            (a.id, b)
            for a, b in await HoldingControlService(db).balances([ctx.filial_id, destination.id])
        )
        assert float(balances[ctx.usd_account_id]) == 849.75
        assert float(balances[dest_id]) == 250.25
