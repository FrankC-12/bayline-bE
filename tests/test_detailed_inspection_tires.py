import pytest
from app.modules.detailed_inspections.schemas import InspectionData, Tire
from app.modules.detailed_inspections.service import missing_fields


def test_tires_need_pressure_only_and_retired_mobile_field_is_ignored():
    reading = Tire.model_validate({"pressure": 32, "tread_depth": 0})
    assert reading.model_dump() == {"pressure": 32}
    assert "tread_depth" not in Tire.model_json_schema()["properties"]
    data = InspectionData.model_validate(
        {
            "tires": {
                position: {"pressure": 32}
                for position in ["front_left", "front_right", "rear_left", "rear_right", "spare"]
            }
        }
    )
    assert not any(field.startswith("tires.") for field in missing_fields(data))


@pytest.mark.asyncio
async def test_editing_pressure_keeps_historical_depth_without_exposing_it():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.modules.detailed_inspections.service import DetailedInspectionService
    import uuid

    row = SimpleNamespace(
        data={"tires": {"front_left": {"pressure": 30, "tread_depth": 6.5}}},
        status="en_proceso",
        updated_by_user_id=None,
    )
    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
    service = DetailedInspectionService(db)
    service.draft = AsyncMock(return_value=row)
    await service.save(
        uuid.uuid4(),
        InspectionData.model_validate(
            {"tires": {"front_left": {"pressure": 32, "tread_depth": 9}}}
        ),
        uuid.uuid4(),
    )
    assert row.data["tires"]["front_left"] == {"pressure": 32, "tread_depth": 6.5}
    assert InspectionData.model_validate(row.data).model_dump()["tires"]["front_left"] == {
        "pressure": 32
    }
