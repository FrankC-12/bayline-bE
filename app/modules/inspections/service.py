import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.inspections.exceptions import (
    CannotDeleteLinkedInspectionError,
    InspectionAlreadyLinkedError,
    InspectionNotFoundError,
)
from app.modules.inspections.models import PreliminaryInspection
from app.modules.inspections.schemas import InspectionCreate, InspectionUpdate


class InspectionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_inspections(
        self, filial_id: uuid.UUID, unlinked_only: bool = False
    ) -> list[PreliminaryInspection]:
        query = select(PreliminaryInspection).where(PreliminaryInspection.filial_id == filial_id)
        if unlinked_only:
            query = query.where(PreliminaryInspection.service_order_id.is_(None))
        query = query.order_by(PreliminaryInspection.created_at.desc())
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_inspection(self, inspection_id: uuid.UUID) -> PreliminaryInspection:
        inspection = await self.db.get(PreliminaryInspection, inspection_id)
        if inspection is None:
            raise InspectionNotFoundError(str(inspection_id))
        return inspection

    async def get_for_order(self, service_order_id: uuid.UUID) -> PreliminaryInspection | None:
        result = await self.db.execute(
            select(PreliminaryInspection).where(
                PreliminaryInspection.service_order_id == service_order_id
            )
        )
        return result.scalar_one_or_none()

    async def create_inspection(
        self, payload: InspectionCreate, inspector_user_id: uuid.UUID
    ) -> PreliminaryInspection:
        inspection = PreliminaryInspection(
            filial_id=payload.filial_id,
            vehicle_id=payload.vehicle_id,
            inspector_user_id=inspector_user_id,
            mileage=payload.mileage,
            notes=payload.notes,
            status=payload.status,
        )
        self.db.add(inspection)
        await self.db.commit()
        await self.db.refresh(inspection)
        return inspection

    async def update_inspection(
        self, inspection_id: uuid.UUID, payload: InspectionUpdate
    ) -> PreliminaryInspection:
        inspection = await self.get_inspection(inspection_id)

        if payload.mileage is not None:
            inspection.mileage = payload.mileage
        if payload.notes is not None:
            inspection.notes = payload.notes
        if payload.status is not None:
            inspection.status = payload.status

        if payload.clear_service_order:
            inspection.service_order_id = None
        elif payload.service_order_id is not None:
            if inspection.service_order_id is not None:
                raise InspectionAlreadyLinkedError()
            inspection.service_order_id = payload.service_order_id

        await self.db.commit()
        await self.db.refresh(inspection)
        return inspection

    async def delete_inspection(self, inspection_id: uuid.UUID) -> None:
        inspection = await self.get_inspection(inspection_id)
        if inspection.service_order_id is not None:
            raise CannotDeleteLinkedInspectionError()
        await self.db.delete(inspection)
        await self.db.commit()