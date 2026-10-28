"""Mobile receives field-level conflicts for both plate and VIN."""

import pytest
from httpx import ASGITransport, AsyncClient
from test_client_creation_http import env, _base_payload

VIN = "1HGCM82633A004352"
OTHER_VIN = "1HGCM82633A004353"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plate,vin,fields",
    [
        ("ab-123cd", OTHER_VIN, {"vehicles.0.plate"}),
        ("XY987ZW", VIN.lower(), {"vehicles.0.vin"}),
        ("AB123CD", VIN, {"vehicles.0.vin", "vehicles.0.plate"}),
    ],
)
async def test_reports_each_conflicting_identity_and_rolls_back_client(env, plate, vin, fields):
    app, filial = env
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        first = await http.post(
            "/api/v1/clients",
            json=_base_payload(
                filial,
                vehicles=[{"brand": "Toyota", "model": "Hilux", "plate": "AB123CD", "vin": VIN}],
            ),
        )
        assert first.status_code == 201
        payload = _base_payload(
            filial,
            document_number="87654321",
            vehicles=[{"brand": "Ford", "model": "Focus", "plate": plate, "vin": vin}],
        )
        response = await http.post("/api/v1/clients", json=payload)
        assert response.status_code == 409, response.text
        body = response.json()
        assert body["errorCode"] == "duplicate_vehicle_identity"
        assert {detail["field"] for detail in body["details"]} == fields
        clients = (await http.get("/api/v1/clients", params={"filial_id": str(filial)})).json()
        assert len(clients) == 1
        payload["vehicles"][0].update(plate="XY987ZW", vin=OTHER_VIN)
        assert (await http.post("/api/v1/clients", json=payload)).status_code == 201


@pytest.mark.asyncio
async def test_existing_vehicle_id_cannot_bypass_duplicate_check_on_create(env):
    app, filial = env
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        first = (
            await http.post(
                "/api/v1/clients",
                json=_base_payload(
                    filial,
                    vehicles=[
                        {"brand": "Toyota", "model": "Hilux", "plate": "AB123CD", "vin": VIN}
                    ],
                ),
            )
        ).json()
        vehicle = first["vehicles"][0]
        response = await http.post(
            "/api/v1/clients",
            json=_base_payload(filial, document_number="87654321", vehicles=[vehicle]),
        )
        assert response.status_code == 409
        assert {d["field"] for d in response.json()["details"]} == {
            "vehicles.0.vin",
            "vehicles.0.plate",
        }


@pytest.mark.asyncio
async def test_unchanged_vehicle_is_allowed_on_edit_but_other_clients_id_is_not(env):
    app, filial = env
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        first = (
            await http.post(
                "/api/v1/clients",
                json=_base_payload(
                    filial,
                    vehicles=[
                        {"brand": "Toyota", "model": "Hilux", "plate": "AB123CD", "vin": VIN}
                    ],
                ),
            )
        ).json()
        response = await http.patch(
            f"/api/v1/clients/{first['id']}", json={"vehicles": first["vehicles"]}
        )
        assert response.status_code == 200, response.text
        second = (
            await http.post(
                "/api/v1/clients", json=_base_payload(filial, document_number="87654321")
            )
        ).json()
        response = await http.patch(
            f"/api/v1/clients/{second['id']}", json={"vehicles": first["vehicles"]}
        )
        assert response.status_code == 409, response.text
        assert {d["field"] for d in response.json()["details"]} == {
            "vehicles.0.vin",
            "vehicles.0.plate",
        }


@pytest.mark.asyncio
async def test_duplicate_identities_inside_same_request_are_both_reported(env):
    app, filial = env
    vehicle = {"brand": "Toyota", "model": "Hilux", "plate": "AB123CD", "vin": VIN}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post(
            "/api/v1/clients", json=_base_payload(filial, vehicles=[vehicle, vehicle])
        )
        assert response.status_code == 409, response.text
        assert {d["field"] for d in response.json()["details"]} == {
            "vehicles.1.vin",
            "vehicles.1.plate",
        }
