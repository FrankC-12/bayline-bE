import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.core.database import Base

from app.modules.holdings.models import Holding  # noqa: F401
from app.modules.filiales.models import Filial  # noqa: F401
from app.modules.roles.models import Role, RoleModulePermission  # noqa: F401
from app.modules.users.models import User, UserModulePermission  # noqa: F401
from app.modules.clients.models import Client, Vehicle  # noqa: F401
from app.modules.service_orders.models import Bay, ServiceOrder  # noqa: F401
from app.modules.inspections.models import PreliminaryInspection  # noqa: F401
from app.modules.parts.models import Part, PartSale, PartSaleLine, PartReturn  # noqa: F401
from app.modules.post_ventas.models import LaborSettings, Tempario, TemparioPart  # noqa: F401
from app.modules.warehouse.models import Warehouse, PartLot, StockMovement, Transfer, TransferLine  # noqa: F401
from app.modules.concesionario.models import DealershipVehicle, VehicleSale  # noqa: F401
from app.modules.administracion.models import Supplier, PurchaseRequest, PurchaseRequestLine, SupplierClaim, Account, IncomeEntry, ExpenseEntry  # noqa: F401

# Import every model here so Alembic can detect it for autogenerate.
# from app.modules.users.models import User  # noqa: F401  <- added when the Users module exists

config = context.config
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
