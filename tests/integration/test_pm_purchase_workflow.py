"""PM flow: partial receipt, supplier invoice, abonos and exact FIFO lineage."""

import uuid
from datetime import date

import pytest
from sqlalchemy import select
from test_workshop_dispatch_postgres import inventory

from app.modules.administracion.enums import SupplierType
from app.modules.administracion.models import ExpenseEntry, Supplier
from app.modules.administracion.purchase_workflow import PurchaseWorkflow
from app.modules.administracion.schemas import (
    PurchasePayment,
    PurchaseReceive,
    PurchaseReconcile,
    PurchaseRequestCreate,
)
from app.modules.administracion.service import AdministracionService
from app.modules.warehouse.enums import TransferStatus
from app.modules.warehouse.models import PartLot
from app.modules.warehouse.schemas import TransferCreate
from app.modules.warehouse.service import AlmacenService


@pytest.mark.asyncio
async def test_receive_partial_replay_reconcile_and_partial_payments(billing_db):
    ctx = billing_db
    part_id, warehouse_id, _ = await inventory(ctx, local=0)
    async with ctx.sessions() as db:
        supplier = Supplier(
            filial_id=ctx.filial_id,
            business_name="QA",
            rif="J-00000001-1",
            supplier_type=SupplierType.NACIONAL,
        )
        db.add(supplier)
        await db.commit()
        order = await AdministracionService(db).create_request(
            PurchaseRequestCreate(
                filial_id=ctx.filial_id,
                supplier_id=supplier.id,
                lines=[dict(part_id=part_id, quantity=10, unit_cost=10)],
            )
        )
        first = PurchaseReceive(
            request_id=uuid.uuid4(),
            warehouse_id=warehouse_id,
            lines=[dict(line_id=order.lines[0].id, quantity=4, location="A1")],
        )
        workflow = PurchaseWorkflow(db)
        result = await workflow.receive(order.id, first, None)
        assert result.status.value == "enviada" and result.lines[0].received_quantity == 4
        result = await workflow.receive(order.id, first, None)
        assert result.lines[0].received_quantity == 4
        await workflow.receive(
            order.id,
            PurchaseReceive(
                request_id=uuid.uuid4(),
                warehouse_id=warehouse_id,
                lines=[dict(line_id=order.lines[0].id, quantity=6, location="B1")],
            ),
            None,
        )
        invoice = await workflow.reconcile(
            order.id,
            PurchaseReconcile(
                invoice_number="123",
                invoice_control_number="C123",
                invoice_date=date.today(),
                credit_days=30,
                iva_percentage=16,
                lines=[dict(line_id=order.lines[0].id, unit_cost=12)],
            ),
            None,
        )
        assert invoice.invoice_total == 139.2
        assert (
            await db.execute(
                select(PartLot.unit_cost).where(PartLot.purchase_request_id == order.id)
            )
        ).scalars().all() == [12, 12]
        account_id = ctx.usd_account_id
        payment = PurchasePayment(
            request_id=uuid.uuid4(),
            account_id=account_id,
            currency="usd",
            amount=50,
            entry_date=date.today(),
        )
        partial = await workflow.pay(order.id, payment, None)
        assert partial.balance_usd == 89.2 and partial.paid_at is None
        replay = await workflow.pay(order.id, payment, None)
        assert replay.balance_usd == 89.2
        final = await workflow.pay(
            order.id,
            PurchasePayment(
                request_id=uuid.uuid4(),
                account_id=account_id,
                currency="usd",
                amount=89.2,
                entry_date=date.today(),
            ),
            None,
        )
        assert final.balance_usd == 0 and final.paid_at is not None
        assert (
            len(
                (
                    await db.execute(
                        select(ExpenseEntry).where(ExpenseEntry.filial_id == ctx.filial_id)
                    )
                )
                .scalars()
                .all()
            )
            == 2
        )


@pytest.mark.asyncio
async def test_transfer_preserves_each_fifo_lot_cost_and_origin(billing_db):
    ctx = billing_db
    part_id, destination, source = await inventory(ctx, local=0, remote=2)
    async with ctx.sessions() as db:
        original = (
            await db.execute(select(PartLot).where(PartLot.part_id == part_id))
        ).scalar_one()
        original.unit_cost = 5
        db.add(
            PartLot(
                filial_id=ctx.filial_id,
                warehouse_id=source,
                part_id=part_id,
                quantity_received=3,
                quantity_remaining=3,
                unit_cost=20,
            )
        )
        await db.commit()
        service = AlmacenService(db)
        transfer = await service.create_transfer(
            TransferCreate(
                filial_id=ctx.filial_id,
                origin_warehouse_id=source,
                destination_warehouse_id=destination,
                lines=[dict(part_id=part_id, quantity=4)],
            )
        )
        await service.update_transfer_status(transfer.id, TransferStatus.EN_PROCESO)
        await service.update_transfer_status(transfer.id, TransferStatus.COMPLETADA)
        lots = (
            (
                await db.execute(
                    select(PartLot).where(
                        PartLot.warehouse_id == destination, PartLot.part_id == part_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sorted((lot.quantity_received, float(lot.unit_cost)) for lot in lots) == [
            (2, 5),
            (2, 20),
        ]
        assert all(lot.origin_lot_id and lot.origin_transfer_id == transfer.id for lot in lots)
        detail = await service.get_lot_detail(original)
        assert len(detail.outbound_movements) == 1
        assert detail.outbound_movements[0].destination_lot_id in [lot.id for lot in lots]


@pytest.mark.asyncio
async def test_bs_payment_reversal_restores_invoice_balance(billing_db):
    from app.modules.administracion.enums import PurchaseRequestStatus
    from app.modules.administracion.models import PurchaseRequest

    ctx = billing_db
    async with ctx.sessions() as db:
        supplier = Supplier(
            filial_id=ctx.filial_id,
            business_name="QA pago",
            rif="J-00000002-1",
            supplier_type=SupplierType.NACIONAL,
        )
        db.add(supplier)
        await db.flush()
        request = PurchaseRequest(
            filial_id=ctx.filial_id,
            sequence_number=1001,
            supplier_id=supplier.id,
            status=PurchaseRequestStatus.CONCILIADA,
            invoice_number="INV",
            invoice_total=100,
        )
        db.add(request)
        await db.commit()
        payment = await PurchaseWorkflow(db).pay(
            request.id,
            PurchasePayment(
                request_id=uuid.uuid4(),
                account_id=ctx.bs_account_id,
                currency="bs",
                amount=500,
                exchange_rate=10,
                entry_date=date.today(),
            ),
            None,
        )
        assert payment.balance_usd == 50
        expense = (
            await db.execute(select(ExpenseEntry).where(ExpenseEntry.filial_id == ctx.filial_id))
        ).scalar_one()
        await AdministracionService(db).reverse_expense(expense.id, None, "Corrección")
        await db.refresh(request)
        assert float(request.paid_amount_usd) == 0
        assert request.paid_at is None
