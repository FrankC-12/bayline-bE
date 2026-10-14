import json
import uuid

import pytest
from sqlalchemy import update

from app.core.audit_logging import (
    AuditLogRuntime,
    register_database_audit,
    request_context,
    set_actor,
)
from app.modules.service_orders.models import ServiceOrder


@pytest.mark.asyncio
async def test_committed_and_bulk_changes_are_audited_in_real_async_postgres(billing_db, tmp_path):
    register_database_audit()
    runtime = AuditLogRuntime(tmp_path).start()
    token = request_context.set({"request_id": "pg-audit-request"})
    actor = uuid.uuid4()
    set_actor(actor, billing_db.filial_id)
    try:
        async with billing_db.sessions() as db:
            order = await db.get(ServiceOrder, billing_db.order_id)
            order.customer_reason = "secret-customer-symptom"
            await db.commit()
            order.customer_reason = "secret-rolled-back"
            await db.flush()
            await db.rollback()
            await db.execute(
                update(ServiceOrder)
                .where(ServiceOrder.id == billing_db.order_id)
                .values(customer_reason="secret-bulk-change")
            )
            await db.commit()
            await db.execute(
                update(ServiceOrder)
                .where(ServiceOrder.id == billing_db.order_id)
                .values(customer_reason="secret-bulk-rollback")
            )
            await db.rollback()
    finally:
        request_context.reset(token)
        runtime.stop()
    records = [
        json.loads(line)
        for file in tmp_path.glob("*/audit.jsonl")
        for line in file.read_text().splitlines()
    ]
    assert [record["operation"] for record in records] == ["update", "bulk_update"]
    assert records[0]["resource_ids"] == {"id": str(billing_db.order_id)}
    assert "customer_reason" in records[0]["changed_fields"]
    assert records[1]["affected_rows"] == 1
    assert all(
        record["user_id"] == str(actor) and record["filial_id"] == str(billing_db.filial_id)
        for record in records
    )
    assert "secret-" not in str(records)
