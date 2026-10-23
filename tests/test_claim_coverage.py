from datetime import date

import pytest

from app.modules.service_orders.claim_coverage import assess_coverage


@pytest.mark.parametrize(
    "today,mileage,valid",
    [
        ("2026-10-23", 10100, True),
        ("2026-12-30", 14999, True),
        ("2026-12-31", 10100, False),
        ("2026-10-23", 15000, False),
        ("2026-10-23", 9999, False),
    ],
)
def test_coverage_uses_reported_reading_and_original_departure(today, mileage, valid):
    snapshot = dict(
        kind="workshop",
        starts_at="2026-10-01",
        expires_at="2026-12-30",
        duration_km=5000,
        expiration_mileage=15000,
    )
    assessment = assess_coverage(snapshot, date.fromisoformat(today), mileage)
    assert assessment["valid"] is valid
    assert assessment["distance"]["start_mileage"] == 10000
    assert assessment["distance"]["used"] == max(0, mileage - 10000)


def test_unknown_and_campaign_coverage_are_never_reported_as_verified():
    assert assess_coverage({}, date(2026, 10, 23), 10000)["valid"] is None
    assert assess_coverage({"kind": "campaign"}, date(2026, 10, 23), 10000)["applicable"] is False
