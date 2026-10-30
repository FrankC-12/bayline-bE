import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableEntityError
from app.modules.clients.models import Vehicle
from app.modules.detailed_inspections.models import DetailedInspection
from app.modules.detailed_inspections.schemas import DetailedInspectionRead, InspectionData

SECTIONS = ("engine", "underbody", "tires", "brakes")


def missing_fields(data: InspectionData) -> list[str]:
    missing = []
    for section in SECTIONS:
        fields = getattr(data, section).model_dump()
        for name, value in fields.items():
            if isinstance(value, dict):
                missing.extend(
                    f"{section}.{name}.{key}" for key, item in value.items() if item is None
                )
            elif value is None:
                missing.append(f"{section}.{name}")
    return missing


def merge_patch(current: dict, patch: dict) -> dict:
    result = dict(current)
    for key, value in patch.items():
        result[key] = merge_patch(result.get(key, {}), value) if isinstance(value, dict) else value
    return result


class DetailedInspectionService:
    def __init__(self, db):
        self.db = db

    async def get(self, order_id):
        return (
            await self.db.execute(
                select(DetailedInspection)
                .where(DetailedInspection.service_order_id == order_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def draft(self, order_id, user_id):
        inspection = await self.get(order_id)
        if inspection is None:
            inspection = DetailedInspection(
                service_order_id=order_id,
                data={},
                annexes=[],
                created_by_user_id=user_id,
                updated_by_user_id=user_id,
                status="en_proceso",
            )
            self.db.add(inspection)
            await self.db.flush()
        return inspection

    def ensure_draft(self, inspection):
        if inspection.status == "completada":
            raise ConflictError(
                "La inspección minuciosa ya está completada.", error_code="inspection_completed"
            )

    async def save(self, order_id, patch, user_id):
        inspection = await self.draft(order_id, user_id)
        self.ensure_draft(inspection)
        merged = merge_patch(inspection.data, patch.model_dump(mode="json", exclude_unset=True))
        data = InspectionData.model_validate(merged).model_dump(mode="json")
        # Keep historical measurements in storage, but never require or expose
        # the retired field or accept new measurements from older mobile apps.
        for position, previous in inspection.data.get("tires", {}).items():
            if isinstance(previous, dict) and "tread_depth" in previous:
                data["tires"].setdefault(position, {})["tread_depth"] = previous["tread_depth"]
        inspection.data = data
        inspection.updated_by_user_id = user_id
        await self.db.commit()
        await self.db.refresh(inspection)
        return inspection

    async def complete(self, order_id, user_id):
        inspection = await self.get(order_id)
        if inspection is None:
            raise NotFoundError("Guarda la inspección antes de completarla.")
        if inspection.status == "completada":
            return inspection
        missing = missing_fields(InspectionData.model_validate(inspection.data))
        if missing:
            raise UnprocessableEntityError(
                "Completa los campos de la revisión antes de finalizar.",
                error_code="detailed_inspection_incomplete",
                details=[
                    {"field": field, "message": "Este campo es obligatorio para completar."}
                    for field in missing
                ],
            )
        inspection.status = "completada"
        inspection.completed_at = datetime.now(UTC)
        inspection.completed_by_user_id = user_id
        inspection.updated_by_user_id = user_id
        await self.db.commit()
        await self.db.refresh(inspection)
        return inspection

    async def add_annex(self, inspection, filename, url, user_id, *, commit=True):
        inspection.annexes = [
            *inspection.annexes,
            {
                "id": str(uuid.uuid4()),
                "filename": filename,
                "url": url,
                "uploaded_at": datetime.now(UTC).isoformat(),
                "uploaded_by_user_id": str(user_id),
            },
        ]
        inspection.updated_by_user_id = user_id
        if commit:
            await self.db.commit()
            await self.db.refresh(inspection)
        return inspection

    async def delete_annex(self, order_id, annex_id, user_id):
        inspection = await self.get(order_id)
        if inspection is None or not any(
            item["id"] == str(annex_id) for item in inspection.annexes
        ):
            raise NotFoundError("No se encontró el anexo.")
        inspection.annexes = [item for item in inspection.annexes if item["id"] != str(annex_id)]
        inspection.updated_by_user_id = user_id
        await self.db.commit()
        await self.db.refresh(inspection)
        return inspection

    async def read(self, inspection, order):
        if inspection is None:
            return None
        vehicle = await self.db.get(Vehicle, order.vehicle_id)
        data = InspectionData.model_validate(inspection.data)
        missing = missing_fields(data)
        sections = {
            section: not any(field.startswith(section + ".") for field in missing)
            for section in SECTIONS
        }
        sections["completion"] = inspection.status == "completada"
        return DetailedInspectionRead(
            id=inspection.id,
            service_order_id=order.id,
            vehicle_id=vehicle.id,
            plate=vehicle.plate,
            vehicle_label=f"{vehicle.brand} {vehicle.model}"
            + (f" {vehicle.year}" if vehicle.year else ""),
            status=inspection.status,
            data=data,
            sections_complete=sections,
            completed_steps=sum(sections.values()),
            missing_fields=missing,
            annexes=inspection.annexes,
            created_by_user_id=inspection.created_by_user_id,
            updated_by_user_id=inspection.updated_by_user_id,
            completed_by_user_id=inspection.completed_by_user_id,
            completed_at=inspection.completed_at,
            created_at=inspection.created_at,
            updated_at=inspection.updated_at,
        )
