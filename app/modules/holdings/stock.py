"""Cross-filial transfer: request → approval → FIFO dispatch → reception.
Each received source lot remains a separate destination lot at the same cost.
"""

import uuid

from sqlalchemy import select

from app.core.exceptions import NotFoundError, UnprocessableEntityError
from app.modules.holdings.control_models import HoldingRegister
from app.modules.parts.enums import PartAvailability
from app.modules.parts.models import Part
from app.modules.warehouse.enums import MovementType
from app.modules.warehouse.models import PartLot, StockMovement, Warehouse
from app.modules.warehouse.service import AlmacenService

ACTIONS = {
    "requested": {"approve": "approved", "reject": "rejected"},
    "approved": {"dispatch": "dispatched", "reject": "rejected"},
    "dispatched": {"receive": "received"},
}


async def stock_registration(db, holding_id, filial_ids, actor_id, record):
    async def warehouse(key):
        row = await db.get(Warehouse, key)
        if not row or row.filial_id not in filial_ids:
            raise NotFoundError("Almacén fuera del holding.")
        if not row.is_active:
            raise UnprocessableEntityError("El almacén está inactivo.")
        return row

    if record.kind == "stock_transfer":
        source = await warehouse(record.source_warehouse_id)
        destination = await warehouse(record.destination_warehouse_id)
        if source.filial_id == destination.filial_id:
            raise UnprocessableEntityError("El traslado del holding requiere filiales distintas.")
        seen = set()
        for line in record.lines:
            if line.source_part_id in seen:
                raise UnprocessableEntityError("El repuesto de origen está repetido.")
            seen.add(line.source_part_id)
            a = await db.get(Part, line.source_part_id)
            b = await db.get(Part, line.destination_part_id)
            if (
                not a
                or not b
                or a.filial_id != source.filial_id
                or b.filial_id != destination.filial_id
                or not a.is_active
                or not b.is_active
            ):
                raise UnprocessableEntityError(
                    "Los repuestos deben estar activos y pertenecer a sus respectivas filiales."
                )
            manufacturer_match = bool(
                a.manufacturer_part_number
                and b.manufacturer_part_number
                and a.manufacturer_part_number.casefold() == b.manufacturer_part_number.casefold()
            )
            if a.unit != b.unit or (a.code != b.code and not manufacturer_match):
                raise UnprocessableEntityError(
                    "El repuesto de destino debe coincidir por código o número de "
                    "fabricante y unidad."
                )
        return {
            "stage": "requested",
            "source_filial_id": str(source.filial_id),
            "destination_filial_id": str(destination.filial_id),
        }
    root = await db.get(HoldingRegister, record.transfer_id)
    if not root or root.holding_id != holding_id or root.kind != "stock_transfer":
        raise NotFoundError("Traslado no encontrado.")
    prior = (
        (
            await db.execute(
                select(HoldingRegister)
                .where(
                    HoldingRegister.holding_id == holding_id,
                    HoldingRegister.kind == "stock_transfer_action",
                )
                .order_by(HoldingRegister.created_at, HoldingRegister.id)
            )
        )
        .scalars()
        .all()
    )
    history = [r for r in prior if r.payload["input"]["transfer_id"] == str(root.id)]
    stage = history[-1].payload["evidence"]["stage"] if history else "requested"
    if record.action not in ACTIONS.get(stage, {}):
        raise UnprocessableEntityError("La acción no corresponde a la etapa actual del traslado.")
    evidence = {**root.payload["evidence"], "stage": ACTIONS[stage][record.action]}
    source = await warehouse(uuid.UUID(root.payload["input"]["source_warehouse_id"]))
    dest = await warehouse(uuid.UUID(root.payload["input"]["destination_warehouse_id"]))
    service = AlmacenService(db)
    if record.action == "dispatch":
        # Same part lock order as the established inventory writers.
        allocations = []
        for line in sorted(root.payload["input"]["lines"], key=lambda x: x["source_part_id"]):
            part_id = uuid.UUID(line["source_part_id"])
            consumed = []
            unit_cost = await service._consume_fifo(
                source.id, part_id, line["quantity"], consumed_lots=consumed
            )
            part = await db.get(Part, part_id)
            part.stock_quantity = max(0, part.stock_quantity - line["quantity"])
            part.availability = (
                PartAvailability.DISPONIBLE if part.stock_quantity > 0 else PartAvailability.AGOTADO
            )
            for lot, quantity in consumed:
                allocations.append(
                    dict(
                        origin_lot_id=str(lot.id),
                        quantity=quantity,
                        unit_cost=str(lot.unit_cost),
                        destination_part_id=line["destination_part_id"],
                    )
                )
            db.add(
                StockMovement(
                    filial_id=source.filial_id,
                    warehouse_id=source.id,
                    part_id=part_id,
                    movement_type=MovementType.TRANSFERENCIA_SALIDA,
                    quantity=line["quantity"],
                    unit_cost=unit_cost,
                    reference=f"HT-{root.id}",
                    responsible_user_id=actor_id,
                    note=record.note,
                )
            )
        evidence["allocations"] = allocations
    if record.action == "receive":
        dispatch = next(r for r in reversed(history) if r.payload["input"]["action"] == "dispatch")
        created = []
        allocations = dispatch.payload["evidence"]["allocations"]
        for part_id in sorted({x["destination_part_id"] for x in allocations}):
            await db.execute(
                select(Part)
                .where(Part.id == uuid.UUID(part_id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        next_number = await service._next_lot_number(dest.filial_id)
        for allocation in allocations:
            part = await db.get(Part, uuid.UUID(allocation["destination_part_id"]))
            lot = PartLot(
                filial_id=dest.filial_id,
                warehouse_id=dest.id,
                part_id=part.id,
                lot_number=next_number,
                quantity_received=allocation["quantity"],
                quantity_remaining=allocation["quantity"],
                unit_cost=allocation["unit_cost"],
                origin_lot_id=uuid.UUID(allocation["origin_lot_id"]),
                note=f"HT-{root.id}: {record.note}",
            )
            next_number += 1
            db.add(lot)
            await db.flush()
            created.append(str(lot.id))
            part.stock_quantity += allocation["quantity"]
            part.availability = PartAvailability.DISPONIBLE
            db.add(
                StockMovement(
                    filial_id=dest.filial_id,
                    warehouse_id=dest.id,
                    part_id=part.id,
                    movement_type=MovementType.TRANSFERENCIA_ENTRADA,
                    quantity=allocation["quantity"],
                    unit_cost=allocation["unit_cost"],
                    reference=f"HT-{root.id}",
                    responsible_user_id=actor_id,
                    note=record.note,
                )
            )
        evidence["destination_lot_ids"] = created
    return evidence
