"""Holding inventario report."""

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.modules.concesionario.models import DealershipVehicle
from app.modules.holdings.control_common import D, ReportContext, aware, table, value
from app.modules.parts.models import Part
from app.modules.warehouse.enums import MovementType
from app.modules.warehouse.models import PartLot, StockMovement, Transfer, Warehouse


async def render(self, ctx: ReportContext):
    vehicles = (
        await self.db.execute(
            select(DealershipVehicle.filial_id, DealershipVehicle.status, func.count())
            .where(DealershipVehicle.filial_id.in_(ctx.ids))
            .group_by(DealershipVehicle.filial_id, DealershipVehicle.status)
        )
    ).all()
    delivered = {
        r.payload["input"]["vehicle_id"] for r in ctx.records if r.kind == "vehicle_delivery"
    }
    units = (
        await self.db.execute(
            select(
                DealershipVehicle.id,
                DealershipVehicle.filial_id,
                DealershipVehicle.brand,
                DealershipVehicle.model,
                DealershipVehicle.vin,
                DealershipVehicle.status,
            ).where(DealershipVehicle.filial_id.in_(ctx.ids))
        )
    ).all()
    counts = defaultdict(int)
    for unit in units:
        counts[
            (unit.filial_id, "entregado" if str(unit.id) in delivered else value(unit.status))
        ] += 1
    vehicles = [(f, status, count) for (f, status), count in counts.items()]
    ctx.report.tables.append(
        table(
            "Vehículos por sucursal",
            [
                ("filial", "Filial"),
                ("vehicle", "Vehículo"),
                ("vin", "VIN"),
                ("status", "Estado", "status"),
            ],
            [
                dict(
                    id=str(u.id),
                    filial_id=str(u.filial_id),
                    filial=ctx.names[u.filial_id],
                    vehicle=f"{u.brand} {u.model}",
                    vin=u.vin,
                    status="entregado" if str(u.id) in delivered else value(u.status),
                )
                for u in units
            ],
        )
    )
    ctx.metric(
        "Vehículos en inventario",
        sum((n for f, s, n in vehicles if value(s) not in {"vendido", "entregado"})),
    )
    ctx.report.tables.append(
        table(
            "Stock global de vehículos",
            [("filial", "Filial"), ("status", "Estado", "status"), ("count", "Unidades", "number")],
            [dict(filial=ctx.names[f], status=value(s), count=n) for f, s, n in vehicles],
            "Entregado requiere confirmar la entrega; vendido pendiente de entrega "
            "se distingue del inventario disponible.",
        )
    )
    stock = (
        select(
            PartLot.part_id,
            func.sum(PartLot.quantity_remaining).label("qty"),
            func.sum(PartLot.quantity_remaining * PartLot.unit_cost).label("cost"),
            func.min(PartLot.received_at).label("oldest"),
        )
        .join(Warehouse, Warehouse.id == PartLot.warehouse_id)
        .where(
            PartLot.filial_id.in_(ctx.ids),
            Warehouse.is_active.is_(True),
            PartLot.quantity_remaining > 0,
        )
        .group_by(PartLot.part_id)
        .subquery()
    )
    last = (
        select(StockMovement.part_id, func.max(StockMovement.created_at).label("last_out"))
        .where(
            StockMovement.filial_id.in_(ctx.ids),
            StockMovement.movement_type.in_(
                [MovementType.SALIDA, MovementType.TRANSFERENCIA_SALIDA]
            ),
        )
        .group_by(StockMovement.part_id)
        .subquery()
    )
    rows = (
        await self.db.execute(
            select(Part, stock.c.qty, stock.c.cost, stock.c.oldest, last.c.last_out)
            .outerjoin(stock, stock.c.part_id == Part.id)
            .outerjoin(last, last.c.part_id == Part.id)
            .where(Part.filial_id.in_(ctx.ids), Part.is_active.is_(True))
            .order_by(Part.code)
        )
    ).all()
    from app.modules.warehouse.reservations import reserved_by_lot

    reservations = await reserved_by_lot(self.db, part_ids=[p.id for p, *_ in rows])
    reserved_parts = defaultdict(int)
    if reservations:
        for lot_id, part_id in (
            await self.db.execute(
                select(PartLot.id, PartLot.part_id).where(PartLot.id.in_(list(reservations)))
            )
        ).all():
            reserved_parts[part_id] += reservations[lot_id]
    cutoff = datetime.now(UTC) - timedelta(days=90)
    parts = []
    for p, qty, cost, oldest, last_out in rows:
        stagnant = bool(qty and oldest and (aware(last_out or oldest) < cutoff))
        parts.append(
            dict(
                id=str(p.id),
                filial_id=str(p.filial_id),
                filial=ctx.names[p.filial_id],
                code=p.code,
                manufacturer=p.manufacturer_part_number or "",
                name=p.name,
                stock=max(0, (qty or 0) - reserved_parts[p.id]),
                physical=qty or 0,
                reserved=reserved_parts[p.id],
                min_stock=p.min_stock,
                cost=float(cost or 0),
                last_out=last_out,
                status=(
                    "Sin rotación"
                    if stagnant
                    else "Stock bajo" if (qty or 0) <= p.min_stock else "Disponible"
                ),
            )
        )
    ctx.metric("Repuestos con stock bajo", sum(p["stock"] <= p["min_stock"] for p in parts))
    ctx.metric(
        "Repuestos sin rotación (90 días)", sum(p["status"] == "Sin rotación" for p in parts)
    )
    ctx.metric("Valor FIFO físico", sum(D(p["cost"]) for p in parts), "money")
    ctx.report.tables.append(
        table(
            "Existencias cruzadas entre sucursales",
            [
                ("code", "Código"),
                ("manufacturer", "N.º fabricante"),
                ("name", "Repuesto"),
                ("filial", "Filial"),
                ("physical", "Físico", "number"),
                ("reserved", "Reservado", "number"),
                ("stock", "Disponible", "number"),
                ("min_stock", "Mínimo", "number"),
                ("cost", "Valor FIFO", "money"),
                ("last_out", "Última salida", "datetime"),
                ("status", "Alerta", "status"),
            ],
            parts,
            (
                "Físico y reservado se distinguen del disponible. Lotes de almacenes "
                "activos; cruza por código o número del fabricante. Sin rotación: sin "
                "salidas durante 90 días."
            ),
        )
    )
    warehouses = (
        (
            await self.db.execute(
                select(Warehouse).where(
                    Warehouse.filial_id.in_(ctx.ids), Warehouse.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    ctx.report.tables.append(
        table(
            "Almacenes del grupo",
            [("filial", "Filial"), ("name", "Almacén")],
            [
                dict(
                    id=str(w.id),
                    filial_id=str(w.filial_id),
                    filial=ctx.names[w.filial_id],
                    name=w.name,
                )
                for w in warehouses
            ],
        )
    )
    actions = {}
    for record in ctx.records:
        if record.kind == "stock_transfer_action":
            actions[record.payload["input"]["transfer_id"]] = record
    roots = [r for r in ctx.records if r.kind == "stock_transfer"]
    ctx.report.tables.append(
        table(
            "Traslados entre filiales",
            [
                ("date", "Solicitud", "date"),
                ("origin", "Origen"),
                ("destination", "Destino"),
                ("note", "Motivo"),
                ("stage", "Etapa", "status"),
                ("quantity", "Unidades", "number"),
            ],
            [
                dict(
                    id=str(r.id),
                    date=r.entry_date,
                    origin=ctx.names.get(uuid.UUID(r.payload["evidence"]["source_filial_id"])),
                    destination=ctx.names.get(
                        uuid.UUID(r.payload["evidence"]["destination_filial_id"])
                    ),
                    note=r.payload["input"]["note"],
                    stage=(
                        actions[str(r.id)].payload["evidence"]["stage"]
                        if str(r.id) in actions
                        else "requested"
                    ),
                    quantity=sum(line["quantity"] for line in r.payload["input"]["lines"]),
                )
                for r in roots
            ],
            (
                "Solicitud → aprobación → despacho FIFO → recepción. El costo y el lote de "
                "origen se conservan en destino."
            ),
        )
    )
    transfers = (
        await self.db.execute(
            select(Transfer, Warehouse.name)
            .join(Warehouse, Warehouse.id == Transfer.origin_warehouse_id)
            .where(Transfer.filial_id.in_(ctx.ids))
            .order_by(Transfer.created_at.desc())
            .limit(200)
        )
    ).all()
    wh = {
        w.id: w.name
        for w in (await self.db.execute(select(Warehouse).where(Warehouse.filial_id.in_(ctx.ids))))
        .scalars()
        .all()
    }
    ctx.report.tables.append(
        table(
            "Monitor de transferencias de piezas",
            [
                ("date", "Fecha", "datetime"),
                ("filial", "Filial"),
                ("code", "Transferencia"),
                ("origin", "Origen"),
                ("destination", "Destino"),
                ("status", "Estado", "status"),
            ],
            [
                dict(
                    date=t.created_at,
                    filial=ctx.names[t.filial_id],
                    code=f"TR-{t.sequence_number}",
                    origin=o,
                    destination=wh.get(t.destination_warehouse_id, "—"),
                    status=value(t.status),
                )
                for t, o in transfers
            ],
            (
                "Últimas 200 transferencias registradas. El circuito de almacén actual "
                "opera dentro de cada filial."
            ),
        )
    )
