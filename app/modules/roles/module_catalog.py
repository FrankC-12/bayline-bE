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
    # Manual income movements (ingreso manual) — deliberately separate from
    # "administracion" so general Finanzas access doesn't imply it; granted
    # per-user via a permission override, not seeded to any role by default.
    # Egreso/reversar/cobrar/rentabilidad used to live under broader modules
    # too (this one, "administracion", "asesor-servicios") — split out below
    # into their own fine-grained permissions since Finanzas actions are
    # sensitive enough to grant independently (e.g. someone who can create
    # an egreso but not reverse one).
    "movimientos-manuales",
    "finanzas-cobrar",
    "finanzas-egreso",
    "finanzas-reversar",
    "finanzas-rentabilidad",
    "finanzas-transferir",
]
