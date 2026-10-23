"""Read-only catalog quotes use the same available FIFO layers as ODS."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from app.modules.warehouse.fifo import allocate_fifo_preview
from app.modules.warehouse.models import PartLot
from app.modules.warehouse.reservations import available_lots, reserved_by_lot


@dataclass
class CatalogCosts:
    costs: dict
    notes: list[str]


async def catalog_fifo_costs(db, filial_id, temparios):
    part_ids = {p.part_id for t in temparios for p in t.parts if p.part_id}
    if not part_ids:
        return {}
    lots = (
        (
            await db.execute(
                select(PartLot)
                .where(PartLot.filial_id == filial_id, PartLot.part_id.in_(part_ids))
                .order_by(PartLot.received_at, PartLot.id)
            )
        )
        .scalars()
        .all()
    )
    by_part = defaultdict(list)
    for lot in lots:
        by_part[lot.part_id].append(lot)
    reserved = await reserved_by_lot(db, part_ids=part_ids)
    result = {}
    for tempario in temparios:
        quantities = defaultdict(int)
        manual_costs = defaultdict(lambda: Decimal(0))
        for part in tempario.parts:
            if part.part_id:
                quantities[part.part_id] += part.quantity
                manual_costs[part.part_id] += Decimal(str(part.unit_cost)) * part.quantity
        costs = {}
        notes = []
        for part_id, quantity in quantities.items():
            history = by_part[part_id]
            available = [
                lot for lot in available_lots(history, reserved) if lot.quantity_remaining > 0
            ]
            allocations, shortfall = allocate_fifo_preview(available, quantity)
            cost = sum(
                (Decimal(str(lot.unit_cost)) * take for lot, take in allocations), Decimal(0)
            )
            fallback = (
                allocations[-1][0].unit_cost
                if allocations
                else history[-1].unit_cost
                if history
                else manual_costs[part_id] / quantity
            )
            costs[part_id] = cost + Decimal(str(fallback)) * shortfall
            if not history:
                name = next(p.name for p in tempario.parts if p.part_id == part_id)
                notes.append(
                    f"{name}: sin compras registradas; se usa el costo estimado del catálogo."
                )
            elif shortfall:
                notes.append(
                    "Existencia insuficiente: el faltante se estima con el último costo conocido."
                )
        result[tempario.id] = CatalogCosts(costs, list(dict.fromkeys(notes)))
    return result
