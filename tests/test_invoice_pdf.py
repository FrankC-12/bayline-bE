import uuid
from copy import deepcopy
from decimal import Decimal
from io import BytesIO

import pytest
from billing_support import invoice_payload
from pypdf import PdfReader
from test_inventory_vehicle_mileage import inventory_env as vehicle_env_fixture
from test_inventory_vehicle_mileage import sale_input, vehicle_input
from test_receivables_include_part_sales import env as parts_env_fixture
from test_receivables_include_part_sales import make_part_sale
from test_service_order_receivables import inventory as inventory_fixture
from test_service_order_receivables import order_inventory as order_inventory_fixture
from test_service_order_receivables import prepare
from test_service_order_receivables import ready as ready_fixture

from app.core.documents.invoice_pdf import from_service_invoice, render_invoice_pdf
from app.modules.service_orders.invoice_document import render_invoice

inventory = inventory_fixture
order_inventory = order_inventory_fixture
ready = ready_fixture


def example():
    return {
        "issuer": "Bayline · Taller Caracas",
        "code": "FAC-ODS-1001",
        "issued_at": "06/10/2026",
        "reference": "Orden ODS-1001",
        "client": "Andrés Pérez",
        "client_document": "V-12345678",
        "address": "Caracas, Venezuela",
        "vehicle": "Toyota Hilux 2022 · Placa AB123CD\nKilometraje de ingreso: 65.000 km",
        "lines": [
            {"description": "Aceite 15W40", "quantity": 3, "unit_price": 13, "total": 39},
            {
                "description": "Cambio de aceite y revisión general",
                "quantity": 1,
                "unit_price": 25,
                "total": 25,
            },
        ],
        "totals": [
            ("Subtotal", 64),
            ("IVA (16%)", 10.24),
            ("IGTF (3% sobre USD 50)", 1.5),
            ("Total", 75.74),
        ],
        "payments": ["Efectivo USD · USD 51,50 (base USD 50 + IGTF USD 1,50)"],
        "pending": 24.24,
        "notes": [
            "Tasa de ejemplo: Bs. 50,00 por USD",
            "Garantía de mano de obra: 90 días / 5.000 km, lo que ocurra primero.",
        ],
        "example": True,
    }


def text_pdf(data):
    result = render_invoice_pdf(data)
    assert result.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(result))
    return reader, "\n".join(page.extract_text() for page in reader.pages)


def test_pdf_renders_customer_accents_amounts_and_literal_markup():
    data = example()
    data["client"] = "Andrés <script>alert(1)</script>"
    reader, text = text_pdf(data)
    assert len(reader.pages) == 1
    assert "Andrés <script>alert(1)</script>" in text
    assert "75.74" in text and "24.24" in text
    assert "65.000 km" in text and "EJEMPLO" in text


def test_many_lines_repeat_headers_and_keep_all_items():
    data = example()
    data["lines"] = [
        {
            "description": f"Servicio número {i} · descripción extensa " * 3,
            "quantity": 1,
            "total": 10,
        }
        for i in range(80)
    ]
    reader, text = text_pdf(data)
    assert len(reader.pages) > 1
    for i in range(80):
        assert f"Servicio número {i} ·" in text
    for page in reader.pages[1:]:
        assert "Descripción" in page.extract_text()
        assert "Página" in page.extract_text()


@pytest.mark.asyncio
async def test_issued_partial_invoice_pdf_uses_frozen_snapshot_and_true_pending(ready):
    await prepare(ready)
    _, session, order, _, _, billing, accounts, _ = ready
    payload = await invoice_payload(billing, order, accounts)
    payload = payload.model_copy(update={"paid_usd": Decimal("10"), "paid_bs": Decimal("0")})
    invoice = await billing.issue(order.id, payload, None)
    original = deepcopy(invoice.document)
    normalized = from_service_invoice(original)
    assert normalized["pending"] == pytest.approx(float(invoice.total_usd) - 10)
    _, text = text_pdf(normalized)
    assert f'{normalized["pending"]:,.2f}' in text
    assert "Saldo pendiente: 0.00" not in render_invoice(original)
    assert f'{normalized["pending"]:,.2f}' in render_invoice(original)
    assert invoice.document == original


@pytest.mark.asyncio
async def test_pdf_route_serves_binary_and_enforces_filial_access(ready):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core.exception_handlers import register_exception_handlers
    from app.modules.auth.dependencies import get_current_user
    from app.modules.auth.schemas import CurrentUser
    from app.modules.service_orders import router as routes

    await prepare(ready)
    service, _, order, _, _, billing, accounts, _ = ready
    invoice = await billing.issue(order.id, await invoice_payload(billing, order, accounts), None)
    user = CurrentUser(
        user_id=uuid.uuid4(),
        role_id=uuid.uuid4(),
        scope="filial",
        email="test@example.com",
        full_name="Admin",
        role_slug="filial-admin",
        filial_id=order.filial_id,
        holding_id=None,
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get(f"/service-orders/{order.id}/invoice/pdf")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert str(invoice.id) in response.headers["content-disposition"]
        assert PdfReader(BytesIO(response.content)).pages
        app.dependency_overrides[get_current_user] = lambda: user.model_copy(
            update={"filial_id": uuid.uuid4()}
        )
        assert (await http.get(f"/service-orders/{order.id}/invoice/pdf")).status_code == 403


parts_env = parts_env_fixture
vehicle_env = vehicle_env_fixture


@pytest.mark.asyncio
async def test_parts_pdf_includes_selected_account_and_partial_pending(parts_env):
    from test_part_sale_payments import account, payment
    from test_part_sales_fifo import AsyncAdapter

    from app.core.exceptions import BadRequestError, ForbiddenError
    from app.modules.auth.schemas import CurrentUser
    from app.modules.parts import router as routes
    from app.modules.parts.enums import PartSaleStatus
    from app.modules.parts.service import PartsService

    admin, session, filial_id = parts_env
    sale = make_part_sale(session, filial_id, 5001, PartSaleStatus.COMPLETADO, 130)
    chosen = account(session, filial_id)
    chosen.name = "Cuenta elegida"
    session.commit()
    service = PartsService(AsyncAdapter(session))
    await service.collect_sale_payment(sale.id, payment(chosen.id, "50"), None)
    user = CurrentUser(
        user_id=uuid.uuid4(),
        email="test@example.com",
        role_id=uuid.uuid4(),
        role_slug="filial-admin",
        scope="filial",
        filial_id=filial_id,
        holding_id=None,
    )
    response = await routes.part_sale_invoice_pdf(sale.id, user, service)
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(response.body)).pages)
    assert "Cuenta elegida" in text and "Saldo pendiente: USD 80.00" in text
    assert "Filtro" in text
    with pytest.raises(ForbiddenError):
        await routes.part_sale_invoice_pdf(
            sale.id, user.model_copy(update={"filial_id": uuid.uuid4()}), service
        )
    sale.status = PartSaleStatus.PENDIENTE
    session.commit()
    with pytest.raises(BadRequestError):
        await routes.part_sale_invoice_pdf(sale.id, user, service)


@pytest.mark.asyncio
async def test_vehicle_pdf_retains_mileage_at_sale(vehicle_env):
    from sqlalchemy import select

    from app.modules.concesionario import router as routes
    from app.modules.concesionario.models import VehicleSale
    from app.modules.concesionario.schemas import VehicleUpdate

    service, session, filial_id, user = vehicle_env
    vehicle = await service.create_vehicle(
        vehicle_input(filial_id, condition="usado", mileage=65000)
    )
    await service.update_vehicle(
        vehicle.id, VehicleUpdate(status="vendido", sale=sale_input()), user
    )
    sale = session.scalars(select(VehicleSale)).one()
    await service.update_vehicle(vehicle.id, VehicleUpdate(mileage=99000), user)
    response = await routes.vehicle_sale_invoice_pdf(
        sale.id, user.model_copy(update={"role_slug": "filial-admin"}), service
    )
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(response.body)).pages)
    assert "65,000 km" in text
    assert "99,000 km" not in text
