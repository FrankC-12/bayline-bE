"""Financial postings, pre-sale evidence, atomic PDI and actual delivery gates."""

import uuid
from datetime import timedelta
from types import SimpleNamespace
import pytest
import pytest_asyncio
from sqlalchemy import select, func
from test_client_leads import lead_env, lead_payload  # noqa: F401
from app.core.venezuela_time import venezuela_today
from app.modules.concesionario.router import router as stock_router
from app.modules.concesionario.opportunity_router import router as opportunity_router
from app.modules.concesionario.workflow_router import router as workflow_router
from app.modules.concesionario.models import DealershipVehicle, VehicleSale
from app.modules.concesionario.enums import VehicleCondition, VehicleStatus
from app.modules.administracion.models import Account, IncomeEntry, ExpenseEntry
from app.modules.administracion.enums import AccountCurrency, AccountType
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.roles.enums import RoleScope, AccessLevel
from app.modules.users.models import User
from app.modules.users.enums import UserStatus


@pytest_asyncio.fixture
async def flow(lead_env):  # noqa: F811 - imported pytest fixture
    e = lead_env
    for router in (stock_router, workflow_router, opportunity_router):
        e.app.include_router(router, prefix="/api/v1")
    role = (await e.db.execute(select(Role).where(Role.slug == "vendedor"))).scalar_one_or_none()
    if not role:
        role = Role(name="Vendedor", slug="vendedor", scope=RoleScope.FILIAL)
        e.db.add(role)
        await e.db.flush()
        e.db.add(
            RoleModulePermission(
                role_id=role.id, module_id="concesionario", access=AccessLevel.EDITAR
            )
        )
    seller = User(
        full_name="Seller",
        email=f"{uuid.uuid4()}@test.com",
        role_id=role.id,
        filial_id=e.filial.id,
        status=UserStatus.ACTIVO,
    )
    unit = DealershipVehicle(
        filial_id=e.filial.id,
        condition=VehicleCondition.NUEVO,
        status=VehicleStatus.DISPONIBLE,
        brand="Toyota",
        model="Hilux",
        year=2026,
        vin=uuid.uuid4().hex[:17].upper(),
        sku=uuid.uuid4().hex[:15],
        price_cash=10000,
        price_financed=11000,
        cost_price=8000,
        price_currency="USD",
        iva_percentage=16,
        igtf_percentage=3,
        luxury_tax_percentage=0,
    )
    account = Account(
        filial_id=e.filial.id,
        name="Cash USD",
        currency=AccountCurrency.USD,
        account_type=AccountType.CAJA,
        opening_balance=0,
        is_active=True,
    )
    e.db.add_all([seller, unit, account])
    await e.db.commit()
    created = await e.http.post("/api/v1/clients/leads", json=lead_payload(e))
    assert created.status_code == 201, created.text
    opp = (
        await e.http.get("/api/v1/vehicle-opportunities", params={"filial_id": str(e.filial.id)})
    ).json()["items"][0]
    yield SimpleNamespace(
        e=e,
        unit=unit,
        seller=seller,
        account=account,
        opp=opp,
        path=f"/api/v1/vehicle-opportunities/{opp['id']}",
        client_id=created.json()["id"],
    )


async def quoted(f, sale_type="contado"):
    r = await f.e.http.post(
        f.path + "/quote",
        json={
            "expected_version": f.opp["version"],
            "vehicle_id": str(f.unit.id),
            "sale_type": sale_type,
        },
    )
    assert r.status_code == 200, r.text
    f.opp = r.json()
    return f.opp


async def reserve(f):
    payload = {
        "client_id": f.client_id,
        "advisor_user_id": str(f.seller.id),
        "deposit_amount": 1500,
        "expires_at": (venezuela_today() + timedelta(days=7)).isoformat(),
        "reason": "Reserva recibida",
        "opportunity_version": f.opp["version"],
        "account_id": str(f.account.id),
        "payment_channel": "cash",
        "payment_reference": "REC-001",
    }
    r = await f.e.http.post(f"/api/v1/dealership-vehicles/{f.unit.id}/reserve", json=payload)
    assert r.status_code == 200, r.text
    f.opp = (await f.e.http.get(f.path)).json()
    return f.opp


async def sale(f):
    payload = {
        "status": "vendido",
        "sale": {
            "client_id": f.client_id,
            "client_name": "Snapshot",
            "advisor_user_id": str(f.seller.id),
            "sale_type": f.opp["sale_type"],
            "payment_method": "usd" if f.opp["sale_type"] == "contado" else None,
            "usd_payment_channel": "cash" if f.opp["sale_type"] == "contado" else None,
            "usd_account_id": str(f.account.id),
            "opportunity_id": f.opp["id"],
            "opportunity_version": f.opp["version"],
        },
    }
    return await f.e.http.patch(f"/api/v1/dealership-vehicles/{f.unit.id}", json=payload)


@pytest.mark.asyncio
async def test_cash_deposit_not_double_counted_and_delivery_requires_real_pdi(flow):
    f = flow
    await quoted(f)
    await reserve(f)
    assert f.opp["workflow"]["deposit"]["amount"] == 1500
    r = await sale(f)
    assert r.status_code == 200, r.text
    f.opp = (await f.e.http.get(f.path)).json()
    assert f.opp["workflow"]["pdi"]["is_draft"] is True
    pdi_id = uuid.UUID(f.opp["workflow"]["pdi"]["id"])
    incomes = (
        (await f.e.db.execute(select(IncomeEntry).where(IncomeEntry.account_id == f.account.id)))
        .scalars()
        .all()
    )
    assert len(incomes) == 2
    assert sum(float(i.amount) for i in incomes) == 11948  # 11600 + 348 IGTF, not +1500 again.
    early = await f.e.http.post(
        f.path + "/deliver", json={"confirmed": True, "reference": "Cliente recibe"}
    )
    assert early.status_code == 409, early.text
    from app.modules.service_orders.models import ServiceOrder, ServiceOrderTask
    from app.modules.service_orders.enums import ServiceOrderStatus, TaskStatus, ServiceOrderPayer

    order = await f.e.db.get(ServiceOrder, pdi_id)
    task = (
        await f.e.db.execute(
            select(ServiceOrderTask).where(ServiceOrderTask.service_order_id == pdi_id)
        )
    ).scalar_one()
    assert task.payer == ServiceOrderPayer.INTERNO
    order.is_draft = False
    order.status = ServiceOrderStatus.COMPLETADO
    task.status = TaskStatus.CANCELADA
    await f.e.db.commit()
    assert (await f.e.http.get(f.path)).json()["workflow"]["pdi"]["ready"] is False
    task.status = TaskStatus.COMPLETADA
    await f.e.db.commit()
    f.opp = (await f.e.http.get(f.path)).json()
    checks = {
        "expected_version": f.opp["version"],
        "documentos_entregados": True,
        "llaves_entregadas": True,
        "funciones_explicadas": True,
        "invitacion_app_enviada": True,
    }
    r = await f.e.http.put(f.path + "/delivery/checklist", json=checks)
    assert r.status_code == 200, r.text
    f.opp = r.json()
    from app.modules.concesionario.sales_workflow import SalesWorkflow
    from app.modules.concesionario.opportunities import OpportunityService

    opp = await OpportunityService(f.e.db).get(uuid.UUID(f.opp["id"]), True)
    await SalesWorkflow(f.e.db).file(
        opp, "acta_entrega", "private://test.pdf", "Acta.pdf", f.e.current
    )
    await f.e.db.commit()
    delivered = await f.e.http.post(
        f.path + "/deliver", json={"confirmed": True, "reference": "Cliente recibe"}
    )
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["stage"] == "entregada"
    again = await f.e.http.post(
        f.path + "/deliver", json={"confirmed": True, "reference": "Reintento"}
    )
    assert again.json()["delivered_at"] == delivered.json()["delivered_at"]
    assert (
        await f.e.db.scalar(
            select(func.count()).select_from(VehicleSale).where(VehicleSale.vehicle_id == f.unit.id)
        )
    ) == 1


@pytest.mark.asyncio
async def test_cancellation_refund_and_retention_once(flow):
    f = flow
    await quoted(f)
    await reserve(f)
    original = f.opp["version"]
    from app.modules.post_ventas.models import LaborSettings

    settings = (
        await f.e.db.execute(select(LaborSettings).where(LaborSettings.filial_id == f.e.filial.id))
    ).scalar_one()
    settings.vehicle_withdrawal_retention_percentage = 75
    await f.e.db.commit()  # Existing reservation retains its agreed 10%.

    r = await f.e.http.post(
        f.path + "/reservation/cancel",
        json={
            "expected_version": original,
            "account_id": str(f.account.id),
            "reference": "REF-100",
            "reason": "Cliente desiste",
        },
    )
    assert r.status_code == 200, r.text
    dep = r.json()["workflow"]["deposit"]
    assert dep["retained_amount"] == 150 and dep["refunded_amount"] == 1350
    expenses = (
        (await f.e.db.execute(select(ExpenseEntry).where(ExpenseEntry.account_id == f.account.id)))
        .scalars()
        .all()
    )
    assert len(expenses) == 1 and float(expenses[0].amount) == 1350
    repeat = await f.e.http.post(
        f.path + "/reservation/cancel",
        json={
            "expected_version": original,
            "account_id": str(f.account.id),
            "reference": "REF-100",
            "reason": "Cliente desiste",
        },
    )
    assert repeat.status_code == 409
    assert (
        await f.e.db.scalar(
            select(func.count())
            .select_from(ExpenseEntry)
            .where(ExpenseEntry.account_id == f.account.id)
        )
    ) == 1


@pytest.mark.asyncio
async def test_financing_requires_documents_approval_contract_and_insurance(flow):
    f = flow
    await quoted(f, "financiado")
    await reserve(f)
    refused = await sale(f)
    assert refused.status_code == 409, refused.text
    await f.e.db.refresh(f.unit)
    await f.e.db.refresh(f.seller)
    await f.e.db.refresh(f.account)
    from app.modules.concesionario.sales_workflow import SalesWorkflow, DOCUMENTS
    from app.modules.concesionario.opportunities import OpportunityService

    for kind in DOCUMENTS:
        opp = await OpportunityService(f.e.db).get(uuid.UUID(f.opp["id"]), True)
        await SalesWorkflow(f.e.db).file(
            opp, kind, "private://test.pdf", kind + ".pdf", f.e.current
        )
        await f.e.db.flush()
    await f.e.db.commit()
    f.opp = (await f.e.http.get(f.path)).json()
    payload = {
        "expected_version": f.opp["version"],
        "provider": "Financiera externa",
        "external_reference": "SOL-001",
    }
    r = await f.e.http.post(f.path + "/financing/submit", json=payload)
    assert r.status_code == 200, r.text
    f.opp = r.json()
    from app.modules.auth.dependencies import get_current_user

    restricted = f.e.current.model_copy(
        update={"user_id": f.seller.id, "role_id": f.seller.role_id, "role_slug": "vendedor"}
    )
    f.e.app.dependency_overrides[get_current_user] = lambda: restricted
    denied = await f.e.http.post(
        f.path + "/financing/decision",
        json={
            **payload,
            "expected_version": f.opp["version"],
            "approved": True,
            "reference": "No autorizado",
        },
    )
    assert denied.status_code == 403
    f.e.app.dependency_overrides[get_current_user] = lambda: f.e.current
    r = await f.e.http.post(
        f.path + "/financing/decision",
        json={
            **payload,
            "expected_version": f.opp["version"],
            "approved": True,
            "reference": "Aprobación formal 123",
        },
    )
    assert r.status_code == 200, r.text
    f.opp = r.json()
    for kind in ("contrato", "poliza"):
        opp = await OpportunityService(f.e.db).get(uuid.UUID(f.opp["id"]), True)
        await SalesWorkflow(f.e.db).file(
            opp, kind, "private://test.pdf", kind + ".pdf", f.e.current
        )
        await f.e.db.flush()
    await f.e.db.commit()
    f.opp = (await f.e.http.get(f.path)).json()
    r = await f.e.http.post(
        f.path + "/financing/closing",
        json={
            "expected_version": f.opp["version"],
            "insurer": "Seguro externo",
            "policy_number": "POL-001",
        },
    )
    assert r.status_code == 200, r.text
    f.opp = r.json()
    r = await sale(f)
    assert r.status_code == 200, r.text
    rows = (
        (await f.e.db.execute(select(IncomeEntry).where(IncomeEntry.account_id == f.account.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1 and float(rows[0].amount) == 1500  # Approval is not a bank payment.
    latest = (await f.e.http.get(f.path)).json()
    pdf = await f.e.http.get(f"/api/v1/vehicle-sales/{latest['sale_id']}/invoice/pdf")
    from io import BytesIO
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages)
    assert "9,500.00" in text and "Abono recibido" in text


@pytest.mark.asyncio
async def test_private_document_upload_and_proforma(flow, tmp_path, monkeypatch):
    f = flow
    await quoted(f, "financiado")
    import app.core.storage as storage

    settings = storage.get_settings().model_copy(
        update={"private_uploads_dir": str(tmp_path), "s3_private_bucket_name": None}
    )
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    uploaded = await f.e.http.post(
        f.path + "/documents/cedula",
        data={"expected_version": str(f.opp["version"])},
        files={"file": ("identidad.pdf", b"%PDF-1.4\nprivate document\n%%EOF", "application/pdf")},
    )
    assert uploaded.status_code == 200, uploaded.text
    document = uploaded.json()["workflow"]["files"]["cedula"]
    assert "reference" not in document
    downloaded = await f.e.http.get(document["download_url"])
    assert downloaded.status_code == 200 and b"private document" in downloaded.content
    assert "no-store" in downloaded.headers["cache-control"]
    pdf = await f.e.http.get(f.path + "/proforma/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    f.e.app.dependency_overrides[
        __import__("app.modules.auth.dependencies", fromlist=["get_current_user"]).get_current_user
    ] = lambda: f.e.current.model_copy(update={"filial_id": uuid.uuid4()})
    assert (await f.e.http.get(document["download_url"])).status_code == 403


@pytest.mark.asyncio
async def test_financing_hook_signature_and_duplicate_decision(flow, monkeypatch):
    f = flow
    await quoted(f, "financiado")
    await reserve(f)
    from app.modules.concesionario.opportunities import OpportunityService
    from app.modules.concesionario.sales_workflow import SalesWorkflow, DOCUMENTS

    opp = await OpportunityService(f.e.db).get(uuid.UUID(f.opp["id"]), True)
    for kind in DOCUMENTS:
        await SalesWorkflow(f.e.db).file(
            opp, kind, "private://test.pdf", kind + ".pdf", f.e.current
        )
    await f.e.db.flush()
    await SalesWorkflow(f.e.db).submit_financing(opp, "Externa", "SOL-100", f.e.current)
    await f.e.db.commit()
    import app.modules.concesionario.workflow_router as hooks
    import json, time, hmac, hashlib

    secret = "test-only-financing-secret-123456789"
    monkeypatch.setattr(
        hooks, "get_settings", lambda: SimpleNamespace(vehicle_financing_webhook_secret=secret)
    )
    body = json.dumps(
        {
            "opportunity_id": f.opp["id"],
            "filial_id": str(f.e.filial.id),
            "event_id": str(uuid.uuid4()),
            "provider": "Externa",
            "external_reference": "SOL-100",
            "approved": True,
            "reference": "Validación externa",
        }
    ).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Motrix-Timestamp": timestamp,
        "X-Motrix-Signature": "invalid",
    }
    bad = await f.e.http.post(
        "/api/v1/integrations/vehicle-financing/decision", content=body, headers=headers
    )
    assert bad.status_code == 401
    headers["X-Motrix-Signature"] = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    good = await f.e.http.post(
        "/api/v1/integrations/vehicle-financing/decision", content=body, headers=headers
    )
    assert good.status_code == 200, good.text
    assert good.json()["workflow"]["financing_status"] == "aprobado"
    again = await f.e.http.post(
        "/api/v1/integrations/vehicle-financing/decision", content=body, headers=headers
    )
    assert again.status_code == 200 and again.json()["version"] == good.json()["version"]
