"""The maintenance reset may touch only its documented workshop scope."""

import json

import pytest
from conftest import invoice_input
from sqlalchemy import text

from app.modules.service_orders.billing import BillingService
from scripts.purge_service_orders import purge


async def database_engine(ctx):
    async with ctx.sessions() as db:
        return db.bind


async def current_state(engine):
    async with engine.connect() as conn:
        return {
            name: await conn.scalar(text(f"SELECT count(*) FROM {name}"))
            for name in (
                "service_orders",
                "clients",
                "vehicles",
                "parts",
                "part_lots",
                "income_entries",
                "accounts",
                "stock_movements",
                "warehouse_transfers",
            )
        }


@pytest.mark.asyncio
async def test_reset_is_preview_by_default_and_keeps_money_inventory_and_catalogs(
    billing_db, tmp_path
):
    ctx = billing_db
    async with ctx.sessions() as db:
        await BillingService(db).issue(ctx.order_id, await invoice_input(ctx), None)
    engine = await database_engine(ctx)
    before = await current_state(engine)
    async with engine.connect() as conn:
        preview = await purge(conn)
    assert preview["delete"]["service_orders"] == before["service_orders"]
    assert await current_state(engine) == before

    backup = tmp_path / "ods-backup.json"
    async with engine.connect() as conn:
        applied = await purge(conn, apply=True, backup=backup)
    after = await current_state(engine)
    assert after["service_orders"] == 0
    for name in before.keys() - {"service_orders"}:
        assert after[name] == before[name], name
    snapshot = json.loads(backup.read_text())
    assert snapshot["counts"] == applied
    assert len(snapshot["tables"]["service_orders"]) == before["service_orders"]
    assert snapshot["tables"]["service_order_invoices"]
    assert snapshot["tables"]["income_entries"]
    assert backup.stat().st_mode & 0o777 == 0o600
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM service_order_tasks")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM service_order_transfers")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM service_order_invoices")) == 0
        for row in snapshot["tables"]["income_entries"]:
            saved = (
                await conn.execute(
                    text("SELECT amount, source_id FROM income_entries WHERE id = :id"),
                    {"id": row["id"]},
                )
            ).one()
            assert float(saved.amount) == float(row["amount"])
            assert saved.source_id is None


@pytest.mark.asyncio
async def test_reset_refuses_to_overwrite_backup_and_does_not_delete(billing_db, tmp_path):
    engine = await database_engine(billing_db)
    before = await current_state(engine)
    path = tmp_path / "existing.json"
    path.write_text("existing backup")
    async with engine.connect() as conn:
        with pytest.raises(FileExistsError):
            await purge(conn, apply=True, backup=path)
    assert await current_state(engine) == before
    assert path.read_text() == "existing backup"


@pytest.mark.asyncio
async def test_reset_rolls_back_every_change_on_failure(billing_db, tmp_path, monkeypatch):
    async with billing_db.sessions() as db:
        await BillingService(db).issue(billing_db.order_id, await invoice_input(billing_db), None)
    engine = await database_engine(billing_db)
    before = await current_state(engine)
    async with engine.connect() as conn:
        original = type(conn).execute

        async def fail(self, statement, *args, **kwargs):
            if self is conn and str(statement).startswith("DELETE FROM service_order_tasks"):
                raise RuntimeError("Simulated failure")
            return await original(self, statement, *args, **kwargs)

        monkeypatch.setattr(type(conn), "execute", fail)
        with pytest.raises(RuntimeError, match="Simulated failure"):
            await purge(conn, apply=True, backup=tmp_path / "rollback.json")
    assert await current_state(engine) == before


@pytest.mark.asyncio
async def test_pending_linked_dispatch_is_cancelled_but_completed_and_unrelated_are_retained(
    billing_db, tmp_path
):
    from app.modules.warehouse.models import Warehouse, Transfer
    from app.modules.warehouse.enums import TransferStatus
    from app.modules.service_orders.models import ServiceOrderTransfer

    async with billing_db.sessions() as db:
        origin = Warehouse(filial_id=billing_db.filial_id, name="Origen")
        destination = Warehouse(filial_id=billing_db.filial_id, name="Destino")
        request = ServiceOrderTransfer(service_order_id=billing_db.order_id)
        db.add_all([origin, destination, request])
        await db.flush()
        records = []
        for index, (status, linked) in enumerate(
            [
                (TransferStatus.PEDIDO, True),
                (TransferStatus.COMPLETADA, True),
                (TransferStatus.PEDIDO, False),
            ]
        ):
            transfer = Transfer(
                filial_id=billing_db.filial_id,
                sequence_number=index + 1,
                origin_warehouse_id=origin.id,
                destination_warehouse_id=destination.id,
                status=status,
                workshop_request_id=request.id if linked else None,
            )
            db.add(transfer)
            records.append(transfer)
        await db.commit()
        ids = [record.id for record in records]
    engine = await database_engine(billing_db)
    async with engine.connect() as conn:
        await purge(conn, apply=True, backup=tmp_path / "dispatches.json")
    async with billing_db.sessions() as db:
        statuses = [(await db.get(Transfer, key)).status for key in ids]
        assert statuses == [
            TransferStatus.CANCELADA,
            TransferStatus.COMPLETADA,
            TransferStatus.PEDIDO,
        ]
        assert all([(await db.get(Transfer, key)).workshop_request_id is None for key in ids])
