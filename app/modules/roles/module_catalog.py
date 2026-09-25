MODULE_CATALOG: list[str] = [
    "asesor-servicios",
    "tecnico-servicio",
    "administracion",
    "usuarios-accesos",
    "post-ventas",
    "kpis",
    "clientes-vehiculos",
    "repuestos",
    "almacen",
    "concesionario",
    "ventas",
    "ajustes",
    # Proveedores / Compras a Proveedores / Reclamos a Proveedor — moved out
    # of "administracion" into their own module; the underlying tables
    # (Supplier, PurchaseRequest, SupplierClaim) stayed in the administracion
    # backend module, only the permission gate changed. Also gates the new
    # vehicle purchase-order flow.
    "compras",
    # Manual Ingresos/Egresos movements — deliberately separate from
    # "administracion" so general Finanzas access doesn't imply it; granted
    # per-user via a permission override, not seeded to any role by default.
    "movimientos-manuales",
]
