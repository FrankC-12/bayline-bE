"""Cross-filial KPI rollup for a Holding's summary dashboard. Same pattern
as BillingService.get_holding_warranty_receivables: list the holding's
filiales, and for each one reuse the exact per-filial method every other
screen already calls — no new business logic, just counting/summing over
what those methods already return."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.clients.service import ClientService
from app.modules.concesionario.service import ConcesionarioService
from app.modules.filiales.models import Filial
from app.modules.holdings.schemas import (
    FilialDashboardRow,
    HoldingDashboardReport,
    OdsSummary,
    OdtSummary,
    SalesSummary,
)
from app.modules.parts.enums import PartSaleStatus
from app.modules.parts.service import PartsService
from app.modules.service_orders.enums import ServiceOrderStatus, TransferStatus
from app.modules.service_orders.service import ServiceOrderService
from app.modules.users.enums import UserStatus
from app.modules.users.service import UserService
from app.modules.warehouse.service import AlmacenService


class HoldingDashboardService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_dashboard(self, holding_id: uuid.UUID) -> HoldingDashboardReport:
        filiales = (
            (await self.db.execute(select(Filial).where(Filial.holding_id == holding_id)))
            .scalars()
            .all()
        )

        orders_service = ServiceOrderService(self.db)
        parts_service = PartsService(self.db)
        concesionario_service = ConcesionarioService(self.db)
        clients_service = ClientService(self.db)
        users_service = UserService(self.db)
        warehouse_service = AlmacenService(self.db)

        rows = []
        for filial in filiales:
            orders = await orders_service.list_orders(filial.id)
            ods = OdsSummary(
                total=len(orders),
                pendiente=sum(1 for o in orders if o.status == ServiceOrderStatus.PENDIENTE),
                en_progreso=sum(1 for o in orders if o.status == ServiceOrderStatus.EN_PROGRESO),
                completado=sum(1 for o in orders if o.status == ServiceOrderStatus.COMPLETADO),
                orden_cerrada=sum(1 for o in orders if o.status == ServiceOrderStatus.ORDEN_CERRADA),
                cancelado=sum(1 for o in orders if o.status == ServiceOrderStatus.CANCELADO),
            )

            transfer_counts = await orders_service.count_transfers_by_status(filial.id)
            odt = OdtSummary(
                total=sum(transfer_counts.values()),
                pendiente=transfer_counts.get(TransferStatus.PENDIENTE, 0),
                pedido=transfer_counts.get(TransferStatus.PEDIDO, 0),
            )

            part_sales = await parts_service.list_sales(filial.id)
            ventas_repuestos = SalesSummary(
                count=len(part_sales),
                total_usd=sum(
                    s.total_with_taxes for s in part_sales if s.status != PartSaleStatus.CANCELADO
                ),
            )

            vehicle_sales = await concesionario_service.list_sales(filial.id)
            ventas_vehiculos = SalesSummary(
                count=len(vehicle_sales),
                total_usd=sum(float(s.final_price) for s in vehicle_sales),
            )

            clientes = await clients_service.list_clients(filial.id)
            users = await users_service.list_users(None, filial.id)
            warehouses = await warehouse_service.list_warehouses(filial.id)

            rows.append(
                FilialDashboardRow(
                    filial_id=filial.id,
                    filial_name=filial.name,
                    ods=ods,
                    odt=odt,
                    ventas_repuestos=ventas_repuestos,
                    ventas_vehiculos=ventas_vehiculos,
                    clientes=len(clientes),
                    usuarios_total=len(users),
                    usuarios_activos=sum(1 for u in users if u.status == UserStatus.ACTIVO),
                    almacenes_total=len(warehouses),
                    almacenes_activos=sum(1 for w in warehouses if w.is_active),
                )
            )

        return HoldingDashboardReport(holding_id=holding_id, filiales=rows)
