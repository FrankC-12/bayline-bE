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
    # Manual Ingresos/Egresos movements — deliberately separate from
    # "administracion" so general Finanzas access doesn't imply it; granted
    # per-user via a permission override, not seeded to any role by default.
    "movimientos-manuales",
]
