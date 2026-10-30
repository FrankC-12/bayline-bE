"""Evidence-backed dealership workflow. Caller commits once with the stock operation."""

import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select

from app.core.exceptions import BadRequestError, ConflictError
from app.core.venezuela_time import venezuela_today
from app.modules.concesionario.enums import VehicleStatus
from app.modules.concesionario.opportunity_models import VehicleOpportunity

DOCUMENTS = {
    "solicitud_credito": "Planilla de solicitud de crédito",
    "cedula": "Cédula de identidad",
    "rif": "RIF",
    "islr": "Declaración de ISLR del último año",
    "constancia_trabajo": "Constancia de trabajo",
    "balance_personal": "Balance personal",
    "referencia_bancaria": "Referencia bancaria",
    "referencia_personal": "Referencia personal firmada",
    "estados_cuenta": "Estados de cuenta (últimos 3 meses)",
}
CLOSING_FILES = {
    "contrato": "Contrato tripartito firmado",
    "poliza": "Póliza de seguro todo riesgo",
    "acta_entrega": "Acta de entrega firmada",
}
DELIVERY_FLAGS = (
    "documentos_entregados",
    "llaves_entregadas",
    "funciones_explicadas",
    "invitacion_app_enviada",
)


def money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def read_workflow(opp):
    data = dict(opp.workflow or {})
    files = data.pop("files", {})
    data["files"] = {
        kind: {k: v for k, v in info.items() if k != "reference"}
        | {"download_url": f"/api/v1/vehicle-opportunities/{opp.id}/documents/{kind}"}
        for kind, info in files.items()
    }
    order = opp.pdi_order
    ready = bool(
        order and not order.is_draft and order.status.value in ("completado", "orden_cerrada")
    )
    ready = (
        ready
        and any(t.status.value == "completada" for t in opp.pdi_tasks)
        and all(t.status.value in ("completada", "cancelada") for t in opp.pdi_tasks)
    )
    data["pdi"] = (
        {
            "id": str(order.id),
            "code": order.code,
            "status": order.status.value,
            "is_draft": order.is_draft,
            "ready": ready,
        }
        if order
        else None
    )
    data["enabled"] = bool(opp.workflow_version)
    data["required_documents"] = DOCUMENTS
    data["closing_files"] = CLOSING_FILES
    data["delivery_ready"] = bool(
        ready
        and all((data.get("delivery") or {}).get(k) for k in DELIVERY_FLAGS)
        and "acta_entrega" in files
    )
    return data


class SalesWorkflow:
    def __init__(self, db):
        self.db = db

    async def locked(self, opportunity_id, expected_version):
        from app.modules.concesionario.models import DealershipVehicle
        from app.modules.concesionario.opportunities import OpportunityService

        snapshot = await self.db.get(VehicleOpportunity, opportunity_id)
        if snapshot and snapshot.vehicle_id:
            await self.db.execute(
                select(DealershipVehicle.id)
                .where(DealershipVehicle.id == snapshot.vehicle_id)
                .with_for_update()
            )
        opp = await OpportunityService(self.db).get(opportunity_id, True)
        OpportunityService(self.db).assert_version(opp, expected_version)
        if not opp.workflow_version:
            raise BadRequestError("Esta oportunidad conserva el flujo anterior.")
        return opp

    def _mutable(self, opp):
        if opp.sale_id or opp.stage == "perdida":
            raise ConflictError("Esta operación ya no admite cambios de financiamiento.")

    async def file(self, opp, kind, reference, filename, actor):
        from app.modules.concesionario.opportunities import event

        if kind not in DOCUMENTS and kind not in CLOSING_FILES:
            raise BadRequestError("Tipo de documento inválido.")
        if kind in DOCUMENTS:
            self._mutable(opp)
            if opp.sale_type != "financiado" or not opp.quoted_at:
                raise BadRequestError("Cotiza la unidad como financiada antes de cargar recaudos.")
            if (opp.workflow or {}).get("financing_status") in ("enviada", "aprobado"):
                raise ConflictError(
                    "Los recaudos enviados están congelados. Registra una nueva solicitud antes de sustituirlos."
                )
        elif kind in ("contrato", "poliza"):
            self._mutable(opp)
            if (opp.workflow or {}).get("financing_status") != "aprobado":
                raise ConflictError("Espera la aprobación del crédito antes del cierre.")
        elif not opp.sale_id or opp.sale.delivered_at:
            raise ConflictError("El acta se adjunta después de facturar y antes de entregar.")
        data = dict(opp.workflow or {})
        data["files"] = {
            **data.get("files", {}),
            kind: {
                "reference": reference,
                "filename": filename[:200],
                "uploaded_at": datetime.now(UTC).isoformat(),
                "uploaded_by": str(actor.user_id),
            },
        }
        opp.workflow = data
        event(opp, "documento_recibido", actor.user_id, (DOCUMENTS | CLOSING_FILES)[kind])

    async def submit_financing(self, opp, provider, external_reference, actor):
        from app.modules.concesionario.opportunities import event, has_reservation

        self._mutable(opp)
        data = dict(opp.workflow or {})
        if opp.sale_type != "financiado" or not has_reservation(opp) or not data.get("deposit"):
            raise ConflictError(
                "Reserva la unidad con un abono registrado antes de iniciar el crédito."
            )
        if set(DOCUMENTS) - set(data.get("files", {})):
            raise ConflictError("Completa todos los recaudos antes de registrar el envío.")
        if data.get("financing_status") in ("enviada", "aprobado"):
            raise ConflictError("La solicitud ya fue enviada.")
        data.update(
            financing_status="enviada",
            provider=provider.strip(),
            external_reference=external_reference.strip(),
            submitted_at=datetime.now(UTC).isoformat(),
            submitted_by=str(actor.user_id),
        )
        opp.workflow = data
        opp.stage = "financiamiento"
        event(
            opp,
            "solicitud_enviada",
            actor.user_id,
            f"{provider.strip()} · {external_reference.strip()}",
        )

    async def decision(
        self, opp, approved, reference, provider, external_reference, actor_id=None, event_id=None
    ):
        from app.modules.concesionario.opportunities import event

        self._mutable(opp)
        data = dict(opp.workflow or {})
        if data.get("provider") != provider or data.get("external_reference") != external_reference:
            raise ConflictError("La decisión no corresponde a la solicitud enviada.")
        if event_id and event_id in data.get("decision_events", []):
            return
        if data.get("financing_status") != "enviada":
            raise ConflictError("No hay una solicitud pendiente de decisión.")
        data.update(
            financing_status="aprobado" if approved else "rechazado",
            decision_reference=reference.strip(),
            decision_at=datetime.now(UTC).isoformat(),
            decision_by=str(actor_id) if actor_id else "integracion",
            decision_events=[*data.get("decision_events", []), *([event_id] if event_id else [])],
        )
        opp.workflow = data
        event(
            opp,
            "credito_aprobado" if approved else "credito_rechazado",
            actor_id,
            reference.strip(),
        )

    async def closing(self, opp, insurer, policy_number, actor):
        from app.modules.concesionario.opportunities import event

        self._mutable(opp)
        data = dict(opp.workflow or {})
        if data.get("financing_status") != "aprobado":
            raise ConflictError("El crédito todavía no está aprobado.")
        data.update(insurer=insurer.strip(), policy_number=policy_number.strip())
        opp.workflow = data
        event(
            opp, "seguro_registrado", actor.user_id, f"{insurer.strip()} · {policy_number.strip()}"
        )

    def validate_sale(self, opp):
        if not opp.workflow_version:
            return
        data = opp.workflow or {}
        if opp.sale_type == "financiado" and not (
            data.get("financing_status") == "aprobado"
            and {"contrato", "poliza"}.issubset(data.get("files", {}))
            and data.get("insurer")
            and data.get("policy_number")
            and data.get("deposit")
        ):
            raise ConflictError(
                "Completa la aprobación, el contrato, el seguro y el abono antes de facturar."
            )
        deposit = data.get("deposit", {})
        if deposit.get("status") == "devuelto":
            raise ConflictError("Esta reserva fue cancelada.")

    async def account(self, account_id, filial_id, currency):
        from app.modules.administracion.models import Account

        account = (
            await self.db.execute(
                select(Account)
                .where(Account.id == account_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if (
            not account
            or account.filial_id != filial_id
            or not account.is_active
            or account.currency.value != currency
        ):
            raise BadRequestError(
                "Selecciona una cuenta activa de esta sucursal y de la moneda indicada."
            )
        return account

    async def deposit(self, opp, payload, actor, rules):
        from app.modules.administracion.enums import (
            AccountCurrency,
            CounterpartyType,
            IncomeSource,
            MovementSourceType,
        )
        from app.modules.administracion.models import IncomeEntry
        from app.modules.administracion.service import AdministracionService

        if (opp.workflow or {}).get("deposit"):
            raise ConflictError("Esta oportunidad ya tiene un abono registrado.")
        if opp.quoted_currency != "USD":
            raise BadRequestError("El abono de esta operación debe estar cotizado en USD.")
        if (
            not payload.account_id
            or not payload.payment_reference
            or len(payload.payment_reference.strip()) < 3
            or not payload.payment_channel
        ):
            raise BadRequestError("Selecciona la cuenta USD, el medio y la referencia del abono.")
        await self.account(payload.account_id, opp.filial_id, "usd")
        AdministracionService(self.db)._assert_open_period(venezuela_today())
        entry = IncomeEntry(
            filial_id=opp.filial_id,
            entry_date=venezuela_today(),
            source=IncomeSource.AUTOMATICO,
            source_type=MovementSourceType.VEHICLE_RESERVATION,
            source_id=opp.id,
            origin_reference=f"OP-{opp.id.hex[:8].upper()}",
            description=f"Abono de reserva · {opp.client_name}"[:200],
            amount=payload.deposit_amount,
            currency=AccountCurrency.USD,
            amount_usd=payload.deposit_amount,
            account_id=payload.account_id,
            counterparty_type=CounterpartyType.CLIENTE,
            counterparty_client_id=opp.client_id,
            reference=payload.payment_reference.strip(),
            registered_by_user_id=actor.user_id,
        )
        self.db.add(entry)
        await self.db.flush()
        opp.workflow = {
            **(opp.workflow or {}),
            "deposit": {
                "amount": float(payload.deposit_amount),
                "currency": "USD",
                "expires_at": payload.expires_at.isoformat(),
                "income_id": str(entry.id),
                "account_id": str(payload.account_id),
                "payment_channel": payload.payment_channel,
                "reference": payload.payment_reference.strip(),
                "retention_percentage": float(rules.vehicle_withdrawal_retention_percentage),
                "status": "registrado",
                "at": datetime.now(UTC).isoformat(),
            },
        }

    async def cancel_reservation(self, opp, payload, actor):
        from app.modules.administracion.enums import (
            AccountCurrency,
            CounterpartyType,
            ExpenseCategory,
            MovementSourceType,
        )
        from app.modules.administracion.models import ExpenseEntry
        from app.modules.administracion.service import AdministracionService
        from app.modules.concesionario.opportunities import event, has_reservation
        from app.modules.concesionario.opportunity_models import ReservationSettlement
        from app.modules.concesionario.service import ConcesionarioService

        self._mutable(opp)
        if not has_reservation(opp) and not (
            opp.vehicle
            and opp.vehicle.reserved_client_id == opp.client_id
            and opp.vehicle.status == VehicleStatus.RESERVADO
        ):
            raise ConflictError("La oportunidad no tiene una reserva por cancelar.")
        data = dict(opp.workflow or {})
        deposit = dict(data.get("deposit", {}))
        if not deposit or deposit.get("status") != "registrado":
            raise ConflictError("Concilia el abono de esta reserva antes de devolverlo.")
        retained = money(
            money(deposit["amount"]) * Decimal(str(deposit["retention_percentage"])) / 100
        )
        refund = money(deposit["amount"]) - retained
        expense = None
        AdministracionService(self.db)._assert_open_period(venezuela_today())
        if refund:
            account = await self.account(payload.account_id, opp.filial_id, "usd")
            balance, _ = await AdministracionService(self.db)._account_balance(account, 1)
            if money(balance) < refund:
                raise ConflictError("La cuenta no tiene saldo suficiente para la devolución.")
            expense = ExpenseEntry(
                filial_id=opp.filial_id,
                entry_date=venezuela_today(),
                category=ExpenseCategory.OTRO,
                beneficiary=opp.client_name,
                description=f"Devolución de reserva · OP-{opp.id.hex[:8].upper()}"[:200],
                amount=refund,
                amount_usd=refund,
                currency=AccountCurrency.USD,
                account_id=account.id,
                source_type=MovementSourceType.VEHICLE_RESERVATION,
                source_id=opp.id,
                counterparty_type=CounterpartyType.CLIENTE,
                counterparty_client_id=opp.client_id,
                reference=payload.reference.strip(),
                registered_by_user_id=actor.user_id,
            )
            self.db.add(expense)
            await self.db.flush()
        self.db.add(
            ReservationSettlement(
                opportunity_id=opp.id,
                filial_id=opp.filial_id,
                deposit_income_id=uuid.UUID(deposit["income_id"]),
                refund_expense_id=expense.id if expense else None,
                retained_amount=retained,
                refund_amount=refund,
                retention_percentage=deposit["retention_percentage"],
                reason=payload.reason.strip(),
                created_by_user_id=actor.user_id,
            )
        )
        deposit.update(
            status="devuelto",
            retained_amount=float(retained),
            refunded_amount=float(refund),
            refund_expense_id=str(expense.id) if expense else None,
            refund_reference=payload.reference.strip(),
        )
        data["deposit"] = deposit
        opp.workflow = data
        unit = opp.vehicle
        ConcesionarioService(self.db)._record_status_change(
            unit, unit.status, VehicleStatus.DISPONIBLE, actor, payload.reason
        )
        unit.status = VehicleStatus.DISPONIBLE
        unit.reserved_client_id = unit.reserved_by_user_id = None
        unit.deposit_amount = unit.reserved_at = unit.reservation_expires_at = None
        opp.stage = "perdida"
        opp.lost_reason = payload.reason.strip()
        event(
            opp,
            "reserva_cancelada",
            actor.user_id,
            f"Retención USD {retained}; devolución USD {refund} · {payload.reference.strip()}",
        )

    async def record_sale_receipts(self, opp, sale, payment, payload, actor):
        from app.modules.administracion.enums import (
            AccountCurrency,
            CounterpartyType,
            IncomeSource,
            MovementSourceType,
        )
        from app.modules.administracion.models import IncomeEntry
        from app.modules.administracion.service import AdministracionService

        if sale.sale_type.value == "financiado":
            return  # Credit approval is not a bank settlement.
        deposit = money((opp.workflow or {}).get("deposit", {}).get("amount", 0))
        due_usd = money(payment["due_usd"]) - deposit
        if due_usd < 0:
            raise BadRequestError(
                "El aporte en USD debe incluir el abono ya recibido. Usa pago mixto para el resto en Bs."
            )
        AdministracionService(self.db)._assert_open_period(venezuela_today())
        for amount, currency, account_id in [
            (due_usd, AccountCurrency.USD, payload.usd_account_id),
            (money(payment["due_bs"]), AccountCurrency.BS, payload.bs_account_id),
        ]:
            if not amount:
                continue
            await self.account(account_id, opp.filial_id, currency.value)
            rate = sale.bcv_rate
            self.db.add(
                IncomeEntry(
                    filial_id=opp.filial_id,
                    entry_date=venezuela_today(),
                    source=IncomeSource.AUTOMATICO,
                    source_type=MovementSourceType.VEHICLE_SALE,
                    source_id=sale.id,
                    origin_reference=f"CV-{sale.sequence_number:04d}",
                    description=f"Saldo de vehículo · {sale.client_name}"[:200],
                    amount=amount,
                    currency=currency,
                    account_id=account_id,
                    counterparty_type=CounterpartyType.CLIENTE,
                    counterparty_client_id=opp.client_id,
                    registered_by_user_id=actor.user_id,
                    exchange_rate=rate if currency == AccountCurrency.BS else None,
                    amount_usd=amount
                    if currency == AccountCurrency.USD
                    else money(amount / Decimal(str(rate))),
                    amount_bs=amount if currency == AccountCurrency.BS else None,
                )
            )

    async def create_pdi(self, opp, actor_id):
        if opp.pdi_order_id:
            return
        from app.modules.ajustes.snapshots import settings_snapshot
        from app.modules.clients.models import Client, Vehicle
        from app.modules.filiales.models import Filial
        from app.modules.post_ventas.enums import TemparioCategory
        from app.modules.post_ventas.models import Tempario
        from app.modules.post_ventas.service import PostVentasService
        from app.modules.service_orders.enums import ServiceOrderPayer
        from app.modules.service_orders.models import (
            ServiceOrder,
            ServiceOrderTask,
            ServiceOrderTypeCatalog,
        )
        from app.modules.service_orders.service import ServiceOrderService

        unit = opp.vehicle
        if not opp.client_id:
            raise ConflictError("La venta necesita un cliente registrado para crear su PDI.")
        await self.db.execute(select(Filial.id).where(Filial.id == opp.filial_id).with_for_update())
        owned = (
            await self.db.execute(
                select(Vehicle)
                .join(Client, Client.id == Vehicle.client_id)
                .where(
                    Client.filial_id == opp.filial_id, func.upper(Vehicle.vin) == unit.vin.upper()
                )
            )
        ).scalar_one_or_none()
        if owned and owned.client_id != opp.client_id:
            raise ConflictError(
                "El VIN está registrado a otro cliente. Actualiza su titularidad antes de facturar."
            )
        if not owned:
            owned = Vehicle(
                client_id=opp.client_id,
                brand=unit.brand,
                model=unit.model,
                year=unit.year,
                vin=unit.vin,
                plate=unit.plate,
                color=unit.color,
                mileage=unit.mileage,
                purchase_date=venezuela_today(),
            )
            self.db.add(owned)
            await self.db.flush()
        kind = (
            await self.db.execute(
                select(ServiceOrderTypeCatalog).where(
                    ServiceOrderTypeCatalog.filial_id == opp.filial_id,
                    ServiceOrderTypeCatalog.code == "pdi",
                )
            )
        ).scalar_one_or_none()
        if not kind:
            kind = ServiceOrderTypeCatalog(
                filial_id=opp.filial_id,
                code="pdi",
                name="PDI · Preentrega",
                is_system=True,
                is_selectable=False,
                is_active=True,
            )
            self.db.add(kind)
            await self.db.flush()
        category = TemparioCategory.MANTENIMIENTO_PREVENTIVO
        task = (
            await self.db.execute(
                select(Tempario)
                .where(
                    Tempario.filial_id == opp.filial_id,
                    Tempario.name == "Inspección de preentrega (PDI)",
                )
                .order_by(Tempario.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if not task:
            sequence = await self.db.scalar(
                select(func.coalesce(func.max(Tempario.sequence_number), 0)).where(
                    Tempario.filial_id == opp.filial_id, Tempario.category == category
                )
            )
            task = Tempario(
                filial_id=opp.filial_id,
                category=category,
                sequence_number=sequence + 1,
                name="Inspección de preentrega (PDI)",
                estimated_hours=0,
                requires_parts=False,
                warranty_mode="none",
            )
            self.db.add(task)
            await self.db.flush()
        order = ServiceOrder(
            filial_id=opp.filial_id,
            vehicle_id=owned.id,
            order_type_id=kind.id,
            advisor_user_id=actor_id,
            is_draft=True,
            creation_source="web",
            sequence_number=await ServiceOrderService(self.db)._next_draft_number(opp.filial_id),
            customer_reason="Inspección de preentrega de vehículo vendido",
            notes=f"PDI interna · Venta CV-{opp.sale.sequence_number:04d} · sin cargo al cliente",
            settings_snapshot=settings_snapshot(
                await PostVentasService(self.db).get_labor_settings(opp.filial_id, commit=False)
            ),
        )
        self.db.add(order)
        await self.db.flush()
        self.db.add(
            ServiceOrderTask(
                service_order_id=order.id,
                tempario_id=task.id,
                code_snapshot=task.code,
                name_snapshot=task.name,
                hours_snapshot=task.estimated_hours,
                payer=ServiceOrderPayer.INTERNO,
            )
        )
        opp.pdi_order_id = order.id
        from app.modules.concesionario.opportunities import event

        event(
            opp,
            "pdi_creada",
            actor_id,
            "ODS interna en borrador; asigna el técnico y envíala al taller.",
        )

    async def delivery_checks(self, opp, checks, actor):
        from app.modules.concesionario.opportunities import event

        if not opp.sale_id or opp.sale.delivered_at:
            raise ConflictError("La entrega ya está cerrada o falta facturar.")
        opp.workflow = {
            **(opp.workflow or {}),
            "delivery": checks,
            "delivery_checked_by": str(actor.user_id),
            "delivery_checked_at": datetime.now(UTC).isoformat(),
        }
        event(opp, "checklist_entrega", actor.user_id)
