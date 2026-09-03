"""
Central place that imports every SQLAlchemy model in the app.

Any script or Alembic migration that touches the ORM should import this
module first, so all foreign keys between modules can be resolved
regardless of which specific model classes it needs directly.
"""

from app.modules.filiales.models import Filial  # noqa: F401
from app.modules.holdings.models import Holding  # noqa: F401
from app.modules.roles.models import Role, RoleModulePermission  # noqa: F401
from app.modules.users.models import User, UserModulePermission  # noqa: F401
from app.modules.clients.models import Client, Vehicle  # noqa: F401
from app.modules.service_orders.models import Bay, ServiceOrder  # noqa: F401
from app.modules.inspections.models import PreliminaryInspection  # noqa: F401
from app.modules.parts.models import Part, PartSale, PartSaleLine, PartReturn  # noqa: F401
from app.modules.post_ventas.models import LaborSettings, Tempario, TemparioPart  # noqa: F401
from app.modules.warehouse.models import Warehouse, PartLot, StockMovement, Transfer, TransferLine  # noqa: F401
from app.modules.concesionario.models import DealershipVehicle, VehicleSale  # noqa: F401
from app.modules.administracion.models import Supplier, PurchaseRequest, PurchaseRequestLine, SupplierClaim, SupplierPaymentAccount, Account, IncomeEntry, ExpenseEntry  # noqa: F401
from app.modules.exchange_rates.models import ExchangeRate  # noqa: F401
