"""Compile the actual executed locks with PostgreSQL, which SQLite ignores."""

import pytest
from billing_support import invoice_payload
from sqlalchemy.dialects import postgresql
from test_service_order_billing import inventory as inventory_fixture
from test_service_order_billing import order_inventory as order_inventory_fixture
from test_service_order_billing import prepare
from test_service_order_billing import ready as ready_fixture

from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.guards import require_editable_order

inventory = inventory_fixture
order_inventory = order_inventory_fixture
ready = ready_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["invoice", "edit", "reopen"])
async def test_lock_only_the_order_with_joined_order_type(ready, monkeypatch, operation):
    service, session, order, _, _, billing, accounts, _ = ready
    payload = None
    if operation == "invoice":
        await prepare(ready)
        payload = await invoice_payload(billing, order, accounts)
    elif operation == "reopen":
        order.status = ServiceOrderStatus.CANCELADO
        session.commit()

    execute = service.db.execute
    locks = []

    async def verify_query(query):
        sql = str(query.compile(dialect=postgresql.dialect()))
        if "FROM service_orders " in sql and "FOR UPDATE" in sql:
            # Keep the eagerly loaded type while excluding its nullable side from the lock.
            assert "LEFT OUTER JOIN service_order_types" in sql
            assert sql.endswith("FOR UPDATE OF service_orders")
            locks.append(sql)
        return await execute(query)

    monkeypatch.setattr(service.db, "execute", verify_query)
    if operation == "invoice":
        invoice = await billing.issue(order.id, payload, None)
        assert invoice.service_order_id == order.id
    elif operation == "edit":
        assert (await require_editable_order(service.db, order.id)).id == order.id
    else:
        assert (await service.reopen_order(order.id, order.advisor_user_id)).id == order.id
    assert locks, "The operation must still lock the order to serialize concurrent writes."
