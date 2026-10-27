"""Explicit, transactional reset of all workshop orders across all filiales.

Default: report only. Apply: --apply --backup /persistent/path/ods-backup.json
Preserves inventory, bank balances, clients, vehicles, catalogs and audit logs.
Uploaded evidence files are deliberately retained. Run during maintenance.
"""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

# Static identifiers and predicates only; never accept SQL from CLI arguments.
ORDERS = "SELECT id FROM service_orders"
TRANSFERS = "SELECT id FROM service_order_transfers"
LINES = "SELECT id FROM service_order_transfer_lines"
INVOICES = "SELECT id FROM service_order_invoices"
INSPECTIONS = f"SELECT id FROM preliminary_inspections WHERE service_order_id IN ({ORDERS})"
CLAIM_FILTER = (
    f"service_order_id IN ({ORDERS}) OR resulting_service_order_id IN ({ORDERS}) "
    "OR id IN (SELECT warranty_claim_id FROM service_orders WHERE warranty_claim_id IS NOT NULL)"
)
CLAIMS = f"SELECT id FROM warranty_claims WHERE {CLAIM_FILTER}"
# Dependents before parents; no TRUNCATE, broad CASCADE, or disabled constraints.
DELETE_PLAN = [
    ("service_order_collection_requests", "TRUE"),
    ("service_order_invoices", "TRUE"),
    ("workshop_warranties", "TRUE"),
    ("upsell_parts", "TRUE"),
    ("upsell_tasks", "TRUE"),
    ("upsells", "TRUE"),
    ("service_order_transfer_lot_allocations", "TRUE"),
    ("service_order_transfer_lines", "TRUE"),
    ("service_order_transfers", "TRUE"),
    ("service_order_tasks", "TRUE"),
    ("inspection_damages", f"inspection_id IN ({INSPECTIONS})"),
    ("preliminary_inspections", f"service_order_id IN ({ORDERS})"),
    ("detailed_inspections", "TRUE"),
    ("warranty_claims", CLAIM_FILTER),
    ("service_orders", "TRUE"),
]
FINANCE_FILTER = "lower(source_type::text) = 'service_order' " f"AND source_id IN ({ORDERS})"
PRESERVE_PLAN = [
    ("income_entries", FINANCE_FILTER),
    ("expense_entries", FINANCE_FILTER),
    ("warehouse_transfers", f"workshop_request_id IN ({TRANSFERS})"),
    ("warehouse_transfer_lines", f"workshop_request_line_id IN ({LINES})"),
    ("supplier_claims", f"warranty_claim_id IN ({CLAIMS})"),
]


async def report(connection):
    counts = {}
    for table, predicate in DELETE_PLAN + PRESERVE_PLAN:
        counts[table] = await connection.scalar(
            text(f"SELECT count(*) FROM {table} WHERE {predicate}")
        )
    branches = (
        (
            await connection.execute(
                text(
                    "SELECT filial_id::text, count(*) AS orders FROM service_orders GROUP BY filial_id ORDER BY filial_id"
                )
            )
        )
        .mappings()
        .all()
    )
    return {
        "delete": {name: counts[name] for name, _ in DELETE_PLAN},
        "preserve_and_unlink": {name: counts[name] for name, _ in PRESERVE_PLAN},
        "filiales": [dict(row) for row in branches],
    }


async def write_backup(connection, path, counts):
    # O_EXCL avoids destroying an earlier backup; restrictive permissions protect client data.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write('{"format":1,"created_at":')
        json.dump(datetime.now(UTC).isoformat(), output)
        output.write(',"scope":"all_filiales_service_orders","counts":')
        json.dump(counts, output)
        output.write(',"tables":{')
        for index, (table, predicate) in enumerate(DELETE_PLAN + PRESERVE_PLAN):
            if index:
                output.write(",")
            output.write(json.dumps(table) + ":[")
            # PostgreSQL serializes exact numeric values; don't convert them to Python floats.
            stream = await connection.stream(
                text(f"SELECT row_to_json(record)::text FROM {table} AS record WHERE {predicate}")
            )
            first = True
            async for row in stream:
                if not first:
                    output.write(",")
                output.write(row[0])
                first = False
            output.write("]")
        output.write("}}\n")
        output.flush()
        os.fsync(output.fileno())


async def purge(connection, *, apply=False, backup=None):
    if connection.dialect.name != "postgresql":
        raise RuntimeError("Este script requiere PostgreSQL.")
    async with connection.begin():
        await connection.execute(text("SET LOCAL lock_timeout = '10s'"))
        await connection.execute(text("SET LOCAL statement_timeout = '120s'"))
        if apply:
            if backup is None:
                raise ValueError("--apply requiere --backup en una ruta persistente.")
            tables = sorted({name for name, _ in DELETE_PLAN + PRESERVE_PLAN})
            await connection.execute(
                text("LOCK TABLE " + ", ".join(tables) + " IN ACCESS EXCLUSIVE MODE")
            )
        else:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
        counts = await report(connection)
        if apply and counts["delete"]["service_orders"]:
            await write_backup(connection, Path(backup), counts)
            # Keep monetary amounts/currencies and stock exactly as recorded. Remove only
            # links that would otherwise point at deleted orders. Original rows are backed up.
            for table in ("income_entries", "expense_entries"):
                await connection.execute(
                    text(f"UPDATE {table} SET source_id = NULL WHERE {FINANCE_FILTER}")
                )
            await connection.execute(
                text(
                    "UPDATE warehouse_transfers SET status = 'CANCELADA', "
                    "note = concat_ws(' | ', note, 'Cancelada por limpieza de ODS') "
                    f"WHERE workshop_request_id IN ({TRANSFERS}) "
                    "AND lower(status::text) IN ('pedido', 'en_proceso')"
                )
            )
            for table, predicate in DELETE_PLAN:
                await connection.execute(text(f"DELETE FROM {table} WHERE {predicate}"))
            remaining = await connection.scalar(text("SELECT count(*) FROM service_orders"))
            if remaining:
                raise RuntimeError("Quedaron ODS; se revierte toda la operación.")
    return counts


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Borrar todas las ODS y su historial vinculado"
    )
    parser.add_argument("--backup", type=Path, help="Archivo JSON nuevo en un volumen persistente")
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--apply requiere --backup")
    # Use the backend container's own configured database, never a hardcoded host/password.
    from app.core.database import engine

    try:
        async with engine.connect() as connection:
            result = await purge(connection, apply=args.apply, backup=args.backup)
        print(json.dumps(result, indent=2))
        print(
            "LIMPIEZA COMPLETADA. ODS restantes: 0."
            if args.apply
            else "SOLO VISTA PREVIA. No se modificó ningún registro."
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
