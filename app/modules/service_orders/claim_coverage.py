"""Coverage as reported on the claim date, independent of today's odometer."""

from datetime import date


def assess_coverage(snapshot, claimed_at, mileage):
    snapshot = snapshot or {}
    if snapshot.get("kind") == "campaign":
        return {"applicable": False, "valid": None, "time": None, "distance": None}
    start = date.fromisoformat(snapshot["starts_at"]) if snapshot.get("starts_at") else None
    end = date.fromisoformat(snapshot["expires_at"]) if snapshot.get("expires_at") else None
    time = None
    valid = start is None or claimed_at >= start
    if start and end:
        elapsed, total = (claimed_at - start).days, (end - start).days
        time = {
            "used": max(0, elapsed),
            "limit": max(0, total),
            "expires_at": end.isoformat(),
            "valid": start <= claimed_at <= end,
        }
        valid = valid and time["valid"]
    limit = snapshot.get("duration_km")
    distance = None
    if limit is not None:
        expiration = snapshot.get("expiration_mileage")
        base = snapshot.get("start_mileage")
        if base is None and expiration is not None:
            base = expiration - limit
        if base is None and snapshot.get("kind") == "factory":
            base = 0
        if base is not None:
            used = mileage - base
            expiration = expiration if expiration is not None else base + limit
            distance = {
                "used": max(0, used),
                "limit": limit,
                "start_mileage": base,
                "expiration_mileage": expiration,
                "valid": 0 <= used < limit,
            }
            valid = valid and distance["valid"]
        else:
            valid = None if valid else False
    if not snapshot:
        valid = None
    return {"applicable": True, "valid": valid, "time": time, "distance": distance}
