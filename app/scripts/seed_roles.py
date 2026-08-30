import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission

ALL_MODULES = [
    "asesor-servicios", "tecnico-servicio", "administracion", "usuarios-accesos",
    "post-ventas", "kpis", "clientes-vehiculos", "repuestos", "almacen",
    "concesionario", "ventas",
]

ROLES_SEED = [
    {
        "name": "Platform",
        "slug": "platform",
        "description": "Root-level account. Manages holdings only.",
        "scope": RoleScope.PLATFORM,
        "permissions": [],
    },
    {
        "name": "Holding",
        "slug": "holding",
        "description": "Manages filiales within a holding and can view all their modules.",
        "scope": RoleScope.HOLDING,
        "permissions": [],
    },
    {
        "name": "Súper Administrador",
        "slug": "filial-admin",
        "description": "Full access to every module within a single filial.",
        "scope": RoleScope.FILIAL,
        "permissions": [{"module_id": m, "access": AccessLevel.EDITAR} for m in ALL_MODULES],
    },
    {
        "name": "Asesor de Servicios",
        "slug": "asesor",
        "description": "Inspecciones, órdenes de servicio y facturación del taller.",
        "scope": RoleScope.FILIAL,
        "permissions": [
            {"module_id": "asesor-servicios", "access": AccessLevel.EDITAR},
            {"module_id": "clientes-vehiculos", "access": AccessLevel.VER},
            {"module_id": "kpis", "access": AccessLevel.VER},
        ],
    },
    {
        "name": "Técnico de Servicio",
        "slug": "tecnico",
        "description": "Acceso móvil a tareas asignadas e inspecciones.",
        "scope": RoleScope.FILIAL,
        "permissions": [
            {"module_id": "tecnico-servicio", "access": AccessLevel.EDITAR},
            {"module_id": "clientes-vehiculos", "access": AccessLevel.VER},
        ],
    },
    {
        "name": "Administrador",
        "slug": "administrador",
        "description": "Compras, reclamos y finanzas del taller.",
        "scope": RoleScope.FILIAL,
        "permissions": [
            {"module_id": "administracion", "access": AccessLevel.EDITAR},
            {"module_id": "post-ventas", "access": AccessLevel.EDITAR},
            {"module_id": "kpis", "access": AccessLevel.VER},
        ],
    },
    {
        "name": "Almacenista",
        "slug": "almacenista",
        "description": "Inventario, movimientos y transferencias de almacén.",
        "scope": RoleScope.FILIAL,
        "permissions": [
            {"module_id": "almacen", "access": AccessLevel.EDITAR},
            {"module_id": "repuestos", "access": AccessLevel.EDITAR},
        ],
    },
    {
        "name": "Vendedor",
        "slug": "vendedor",
        "description": "Ventas y catálogo del concesionario.",
        "scope": RoleScope.FILIAL,
        "permissions": [
            {"module_id": "concesionario", "access": AccessLevel.EDITAR},
            {"module_id": "ventas", "access": AccessLevel.EDITAR},
            {"module_id": "clientes-vehiculos", "access": AccessLevel.VER},
        ],
    },
]


async def seed_roles() -> None:
    async with AsyncSessionLocal() as session:
        for data in ROLES_SEED:
            existing = await session.execute(select(Role).where(Role.slug == data["slug"]))
            if existing.scalar_one_or_none() is not None:
                print(f"Skipping '{data['slug']}', already exists.")
                continue

            role = Role(
                name=data["name"],
                slug=data["slug"],
                description=data["description"],
                scope=data["scope"],
            )
            session.add(role)
            await session.flush()

            for perm in data["permissions"]:
                session.add(
                    RoleModulePermission(
                        role_id=role.id, module_id=perm["module_id"], access=perm["access"]
                    )
                )

            print(f"Created role '{data['slug']}'.")

        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed_roles())
