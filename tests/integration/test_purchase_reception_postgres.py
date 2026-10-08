import asyncio

import pytest
from sqlalchemy import select

from app.modules.administracion.enums import SupplierType
from app.modules.administracion.models import Supplier
from app.modules.compras.exceptions import InvalidVehiclePurchaseOrderStatusTransitionError
from app.modules.compras.schemas import (
    ReceptionCreate,
    ReceptionUnitInput,
    VehiclePurchaseOrderCreate,
    VehiclePurchaseOrderLineInput,
)
from app.modules.compras.service import ComprasService
from app.modules.concesionario.models import DealershipVehicle


@pytest.mark.asyncio
async def test_concurrent_purchase_receptions_cannot_exceed_ordered_units(billing_db):
    ctx = billing_db
    async with ctx.sessions() as db:
        supplier = Supplier(
            filial_id=ctx.filial_id,
            business_name="Importador",
            rif="J-12345678-9",
            supplier_type=SupplierType.IMPORTADOR,
        )
        db.add(supplier)
        await db.commit()
        order = await ComprasService(db).create_vehicle_purchase_order(
            VehiclePurchaseOrderCreate(
                filial_id=ctx.filial_id,
                supplier_id=supplier.id,
                lines=[
                    VehiclePurchaseOrderLineInput(
                        brand="Toyota", model="Corolla", year=2026, quantity=1
                    )
                ],
            ),
            None,
        )

    async def receive(vin):
        async with ctx.sessions() as db:
            return await ComprasService(db).add_reception(
                order.id,
                ReceptionCreate(
                    units=[ReceptionUnitInput(purchase_order_line_id=order.lines[0].id, vin=vin)]
                ),
                None,
            )

    results = await asyncio.gather(
        receive("1HGCM82633A000001"), receive("1HGCM82633A000002"), return_exceptions=True
    )
    assert (
        sum(isinstance(r, InvalidVehiclePurchaseOrderStatusTransitionError) for r in results) == 1
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    async with ctx.sessions() as db:
        units = (
            await db.scalars(
                select(DealershipVehicle).where(DealershipVehicle.filial_id == ctx.filial_id)
            )
        ).all()
        assert len(units) == 1
