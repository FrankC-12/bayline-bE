import asyncio

import pytest
from sqlalchemy import select

from app.core.exceptions import ConflictError
from app.modules.clients.models import Client, Vehicle
from app.modules.clients.schemas import ClientUpdate
from app.modules.clients.service import ClientService


@pytest.mark.asyncio
async def test_simultaneous_client_updates_only_accept_one_original_version(billing_db):
    ctx = billing_db
    async with ctx.sessions() as db:
        client = (
            await db.execute(select(Client).where(Client.filial_id == ctx.filial_id))
        ).scalar_one()
        client_id, version = client.id, client.updated_at

    async def send(phone):
        async with ctx.sessions() as db:
            try:
                await ClientService(db).update_client(
                    client_id, ClientUpdate(expected_updated_at=version, phone_primary=phone)
                )
                return "saved"
            except ConflictError as error:
                return error.error_code

    results = await asyncio.gather(send("04121234561"), send("04121234562"))
    assert sorted(results) == ["client_version_conflict", "saved"]
    async with ctx.sessions() as db:
        assert (await db.get(Client, client_id)).phone_primary in {"04121234561", "04121234562"}


@pytest.mark.asyncio
async def test_vehicle_changes_advance_client_version_and_legacy_updates_still_work(billing_db):
    ctx = billing_db
    async with ctx.sessions() as db:
        client = (
            await db.execute(select(Client).where(Client.filial_id == ctx.filial_id))
        ).scalar_one()
        vehicle = (
            await db.execute(select(Vehicle).where(Vehicle.client_id == client.id))
        ).scalar_one()
        client_id, version = client.id, client.updated_at
        updated = await ClientService(db).update_client(
            client_id,
            ClientUpdate(
                expected_updated_at=version,
                vehicles=[
                    {
                        "id": vehicle.id,
                        "brand": "Toyota",
                        "model": "Hilux",
                        "plate": "AB123CD",
                        "mileage": 12345,
                    }
                ],
            ),
        )
        assert updated.updated_at != version
        with pytest.raises(ConflictError):
            await ClientService(db).update_client(
                client_id, ClientUpdate(expected_updated_at=version, full_name="Borrador viejo")
            )
        # Existing mobile clients that omit the optional version keep their contract.
        legacy = await ClientService(db).update_client(
            client_id, ClientUpdate(phone_primary="04121234569")
        )
        assert legacy.phone_primary == "04121234569"
        assert legacy.full_name != "Borrador viejo"
