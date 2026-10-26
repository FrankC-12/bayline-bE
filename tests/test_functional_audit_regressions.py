"""Business regressions for the functional audit, using real service methods."""

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select
import test_service_order_part_fifo_pricing as parts_fixtures
import test_completion_requires_finished_work as work_fixtures
import test_pending_upsells_cross_visit as visits_fixtures
import test_holding_control as holding_fixtures
import test_client_creation_plate as clients_fixtures
from workshop_ready import inspections_ready

from app.core.exceptions import ConflictError, UnprocessableEntityError
from app.modules.clients.schemas import ClientCreate, VehicleInput
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.service_orders.enums import ServiceOrderStatus, TaskStatus
from app.modules.service_orders.drafts import DraftService
from app.modules.service_orders.guards import require_no_active_visit
from app.modules.service_orders.schemas import ServiceOrderDraftCreate

parts_env = parts_fixtures.env
work_env = work_fixtures.env
visits_env = visits_fixtures.env
holding_env = holding_fixtures.env
clients_env = clients_fixtures.env


@pytest.mark.asyncio
async def test_list_price_survives_fifo_changes_and_is_used_by_catalog_quote(parts_env):
    from app.modules.post_ventas.models import Tempario, TemparioPart
    from app.modules.post_ventas.enums import TemparioCategory
    from app.modules.post_ventas.service import PostVentasService

    service, session, order, part, warehouse, older, newer = parts_env
    part.list_price = Decimal("12.70")
    task = Tempario(
        filial_id=order.filial_id,
        category=TemparioCategory.MOTOR,
        sequence_number=1,
        name="Cambio",
        estimated_hours=1,
    )
    session.add(task)
    session.flush()
    session.add(
        TemparioPart(tempario_id=task.id, part_id=part.id, name=part.name, quantity=1, unit_cost=20)
    )
    session.commit()
    catalog = await PostVentasService(service.db).get_tempario(task.id)
    assert catalog.parts[0].unit_price == 12.70
    transfer = await service.add_transfer_line(order.id, part.id, 1)
    assert float(transfer.lines[0].line_total) == 12.70
    older.quantity_remaining = 0
    part.list_price = Decimal("99.00")
    session.commit()
    await service.mark_transfer_ordered(transfer.id)
    await service.complete_transfer(transfer.id)
    assert float(transfer.lines[0].cost_total) == 12
    assert float(transfer.lines[0].line_total) == 12.70


@pytest.mark.asyncio
async def test_task_completion_checks_inspections_even_when_parts_are_received(work_env):
    service, order, catalog, actor = work_env
    task = await service.add_task(order.id, catalog.id)
    transfer = (await service.list_transfers(order.id))[0]
    from datetime import UTC, datetime

    transfer.picked_up_at = datetime.now(UTC)
    task.timer_accumulated_seconds = 60
    await service.db.commit()
    with pytest.raises(ConflictError) as error:
        await service.update_task_status(task.id, TaskStatus.COMPLETADA)
    assert error.value.error_code == "work_inspections_required"
    await inspections_ready(service, order)
    await service.update_task_status(task.id, TaskStatus.COMPLETADA)
    assert order.status == ServiceOrderStatus.COMPLETADO


@pytest.mark.asyncio
async def test_zero_time_task_does_not_complete_even_with_inspections(work_env):
    service, order, catalog, actor = work_env
    task = await service.add_task(order.id, catalog.id)
    transfer = (await service.list_transfers(order.id))[0]
    from datetime import UTC, datetime

    transfer.picked_up_at = datetime.now(UTC)
    await inspections_ready(service, order)
    with pytest.raises(ConflictError) as error:
        await service.update_task_status(task.id, TaskStatus.COMPLETADA)
    assert error.value.error_code == "task_not_started"


@pytest.mark.asyncio
async def test_active_visit_is_reported_with_its_business_code(visits_env):
    service, session, settings, origin, second, other, catalog = visits_env
    with pytest.raises(ConflictError) as error:
        await require_no_active_visit(service.db, origin.vehicle_id, origin.filial_id)
    assert error.value.error_code == "vehicle_has_active_order"
    assert any(order.code in error.value.message for order in (origin, second))


@pytest.mark.asyncio
async def test_empty_drafts_do_not_consume_the_official_ods_sequence(visits_env):
    service, session, settings, origin, second, other, catalog = visits_env
    before = await service._next_sequence_number(origin.filial_id)
    flow = DraftService(service.db)
    actor = SimpleNamespace(user_id=origin.advisor_user_id)
    one = await flow.create(ServiceOrderDraftCreate(filial_id=origin.filial_id), actor)
    two = await flow.create(ServiceOrderDraftCreate(filial_id=origin.filial_id), actor)
    assert one.code.startswith("BOR-") and one.code != two.code
    assert await service._next_sequence_number(origin.filial_id) == before
    await flow.discard(one.id)
    assert await service._next_sequence_number(origin.filial_id) == before


@pytest.mark.asyncio
async def test_sent_order_cannot_be_deleted(work_env):
    service, order, catalog, actor = work_env
    with pytest.raises(ConflictError) as error:
        await service.delete_order(order.id)
    assert error.value.error_code == "order_delete_requires_draft"


@pytest.mark.asyncio
async def test_holding_cannot_count_a_dealers_cash(holding_env):
    from app.modules.holdings.control_schemas import RegisterCreate

    session, service, holding, other, actor, filiales, accounts = holding_env
    with pytest.raises(UnprocessableEntityError):
        await service.register(
            holding.id,
            actor.id,
            RegisterCreate(
                request_id=uuid.uuid4(),
                entry_date=date.today(),
                record={
                    "kind": "cash_count",
                    "filial_id": filiales[0].id,
                    "account_id": accounts[0].id,
                    "counted": "1000",
                },
            ),
        )


@pytest.mark.asyncio
async def test_normalized_duplicate_plate_is_rejected_across_clients(clients_env):
    service, filial_id = clients_env

    def payload(number, plate):
        return ClientCreate(
            filial_id=filial_id,
            full_name="Cliente",
            client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V,
            document_number=number,
            phone_primary="04121234567",
            address="Caracas",
            vehicles=[VehicleInput(brand="Toyota", model="Hilux", plate=plate)],
        )

    await service.create_client(payload("12345678", "AB123CD"))
    with pytest.raises(ConflictError) as error:
        await service.create_client(payload("23456789", "ab123cd"))
    assert error.value.error_code == "duplicate_vehicle_identity"


@pytest.mark.parametrize(
    "vin", ["1HGCM82633A00435I", "1HGCM82633A00435O", "1HGCM82633A00435Q", "1HGCM82633A00435-"]
)
def test_forbidden_vin_characters_are_rejected(vin):
    with pytest.raises(ValidationError):
        VehicleInput(brand="Toyota", model="Hilux", vin=vin)


def test_next_model_year_is_valid_but_distant_future_is_not():
    VehicleInput(brand="Toyota", model="Hilux", year=date.today().year + 1)
    with pytest.raises(ValidationError):
        VehicleInput(brand="Toyota", model="Hilux", year=date.today().year + 2)


@pytest.mark.asyncio
async def test_approved_upsell_tracks_only_the_added_work(visits_env):
    from app.modules.service_orders.schemas import (
        UpsellCreate,
        UpsellTaskInput,
        UpsellDecisionInput,
    )

    service, session, settings, order, second, other, catalog = visits_env
    await service.add_task(order.id, catalog.id)  # Unrelated work already on the ODS.
    finding = await service.create_upsell(
        order.id,
        UpsellCreate(
            title="Extra",
            description="Servicio adicional",
            severity="pronto",
            tasks=[UpsellTaskInput(tempario_id=catalog.id)],
        ),
        [],
    )
    approved = await service.decide_upsell(
        finding.id,
        UpsellDecisionInput(status="aprobado", approval_channel="presencial"),
        order.advisor_user_id,
    )
    expected = float(catalog.estimated_hours) * float(settings.hourly_rate)
    assert approved.approved_amount == expected
    assert (await service.get_order_summary(order.id)).labor_subtotal == expected * 2
    settings.hourly_rate = 999
    session.commit()
    assert (await service.get_upsell(finding.id)).approved_amount == expected


@pytest.mark.asyncio
async def test_fiscal_identity_is_saved_and_can_be_corrected(holding_env):
    from app.modules.filiales.service import FilialService
    from app.modules.filiales.schemas import FilialCreate, FilialUpdate

    session, service, holding, other, actor, filiales, accounts = holding_env
    filial = await FilialService(service.db).create_filial(
        FilialCreate(
            holding_id=holding.id,
            name="Concesionario independiente",
            slug="afiliado",
            legal_name="Sociedad independiente",
            rif="J-12345678-9",
            fiscal_address="Av. principal, Caracas",
            business_type="mixto",
            represented_brands="Ford",
            special_taxpayer=True,
            invoice_series="A1",
        )
    )
    assert filial.rif == "J-12345678-9" and filial.special_taxpayer is True
    corrected = await FilialService(service.db).update_filial(
        filial.id, FilialUpdate(special_taxpayer=False, invoice_series="A2")
    )
    assert corrected.special_taxpayer is False and corrected.invoice_series == "A2"


@pytest.mark.asyncio
async def test_workshop_invoice_separates_parts_labor_and_taxes(holding_env):
    from datetime import UTC, datetime
    from app.modules.clients.models import Client, Vehicle
    from app.modules.service_orders.models import ServiceOrder, ServiceOrderInvoice
    from app.modules.service_orders.service import seed_default_order_types
    from app.modules.administracion.service import AdministracionService
    from app.modules.service_orders.models import ServiceOrderTypeCatalog

    session, service, holding, other, actor, filiales, accounts = holding_env
    client = Client(
        filial_id=filiales[0].id,
        full_name="Cliente",
        client_type=ClientType.PARTICULAR,
        document_type=DocumentType.V,
        document_number="12345678",
        phone_primary="04121234567",
        address="Caracas",
    )
    session.add(client)
    session.flush()
    vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Hilux")
    session.add(vehicle)
    session.flush()
    await seed_default_order_types(service.db, filiales[0].id)
    session.flush()
    kind = session.scalars(
        select(ServiceOrderTypeCatalog).where(
            ServiceOrderTypeCatalog.filial_id == filiales[0].id,
            ServiceOrderTypeCatalog.code == "regular",
        )
    ).one()
    order = ServiceOrder(
        filial_id=filiales[0].id, vehicle_id=vehicle.id, sequence_number=2001, order_type_id=kind.id
    )
    session.add(order)
    session.flush()
    now = datetime.now(UTC)
    session.add(
        ServiceOrderInvoice(
            service_order_id=order.id,
            request_id=uuid.uuid4(),
            request_hash="audit",
            code="FAC-1",
            issued_at=now,
            total_usd=145,
            billed_client_id=client.id,
            amount_paid_at_issuance=0,
            document={"summary": {"parts_subtotal": 100, "labor_subtotal": 25}},
        )
    )
    session.commit()
    from zoneinfo import ZoneInfo

    day = now.astimezone(ZoneInfo("America/Caracas")).date()
    report = await AdministracionService(service.db).get_profitability(filiales[0].id, day, day)
    rows = {row.key: row for row in report.departments}
    assert rows["taller_mano_obra"].net_sales == 25
    assert rows["repuestos"].net_sales == 100
    assert report.net_sales_total == 125
    from app.modules.service_orders.service import ServiceOrderService

    order.status = ServiceOrderStatus.COMPLETADO
    order.invoiced_at = now
    session.commit()
    orders = ServiceOrderService(service.db)
    assert (await orders.get_order(order.id)).workshop_stage == "por_cobrar"
    invoice = session.scalars(
        select(ServiceOrderInvoice).where(ServiceOrderInvoice.service_order_id == order.id)
    ).one()
    invoice.collected_at = now
    invoice.amount_paid_at_issuance = 145
    session.commit()
    assert (await orders.get_order(order.id)).workshop_stage == "por_entregar"


@pytest.mark.asyncio
async def test_supplier_payment_requires_the_payable_it_settles(holding_env):
    from app.modules.administracion.schemas import ExpenseEntryCreate
    from app.modules.administracion.service import AdministracionService
    from app.core.exceptions import BadRequestError

    session, service, holding, other, actor, filiales, accounts = holding_env
    payload = ExpenseEntryCreate(
        filial_id=filiales[0].id,
        entry_date=date.today(),
        category="compras_proveedores",
        beneficiary="Proveedor",
        description="Pago SC-1001",
        amount=120,
        currency="usd",
        account_id=accounts[0].id,
        counterparty_type="proveedor",
        counterparty_supplier_id=uuid.uuid4(),
    )
    with pytest.raises(BadRequestError) as error:
        await AdministracionService(service.db).create_expense(payload, None, actor.id)
    assert error.value.error_code == "supplier_payment_requires_payable"


@pytest.mark.asyncio
async def test_parts_revenue_and_receivable_age_start_at_delivery(holding_env):
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo
    from app.modules.parts.models import PartSale, PartSaleLine, Part
    from app.modules.parts.enums import PartSaleStatus
    from app.modules.administracion.service import AdministracionService

    session, service, holding, other, actor, filiales, accounts = holding_env
    now = datetime.now(UTC)
    day = now.astimezone(ZoneInfo("America/Caracas")).date()
    part = Part(
        filial_id=filiales[0].id,
        category_id=uuid.uuid4(),
        code="AUDIT",
        name="Filtro",
        price=0,
        list_price=100,
    )
    session.add(part)
    session.flush()
    sale = PartSale(
        filial_id=filiales[0].id,
        sequence_number=5001,
        client_name="Cliente",
        status=PartSaleStatus.COMPLETADO,
        created_at=now - timedelta(days=40),
        completed_at=now,
    )
    session.add(sale)
    session.flush()
    session.add(
        PartSaleLine(
            part_sale_id=sale.id,
            part_id=part.id,
            quantity=1,
            unit_price=100,
            line_total=100,
            unit_cost=50,
        )
    )
    session.commit()
    admin = AdministracionService(service.db)
    report = await admin.get_profitability(filiales[0].id, day, day)
    assert next(row for row in report.departments if row.key == "repuestos").net_sales == 100
    receivable = (await admin.list_receivables(filiales[0].id))[0]
    assert receivable.days_outstanding == 0
    assert receivable.issued_at == sale.completed_at
