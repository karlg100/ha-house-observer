"""Deterministic occupancy and stay resolution."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import parse_timestamp
from .reservations import normalize_reservations

STAY_ENTITY_ID = "house_observer.stay_context"

_OCCUPIED_STATES = {
    "on",
    "home",
    "occupied",
    "present",
    "detected",
    "true",
    "yes",
    "active",
    "motion_detected",
}
_VACANT_STATES = {
    "off",
    "not_home",
    "away",
    "unoccupied",
    "vacant",
    "clear",
    "false",
    "no",
    "idle",
    "inactive",
}


def occupancy_from_state(state: str | None) -> str:
    """Interpret a helper, binary sensor, or counter as occupied or vacant."""
    if state is None:
        return "unknown"
    value = str(state).strip().lower()
    if value in _OCCUPIED_STATES:
        return "occupied"
    if value in _VACANT_STATES:
        return "vacant"
    try:
        return "occupied" if float(value) > 0 else "vacant"
    except ValueError:
        return "unknown"


def stay_expired(stay: dict[str, Any], now: datetime) -> bool:
    """Return whether a manually supplied stay has passed its check-out."""
    check_out = parse_timestamp(stay.get("check_out"))
    if check_out is None:
        return False
    if check_out.tzinfo is None and now.tzinfo is not None:
        check_out = check_out.replace(tzinfo=now.tzinfo)
    return check_out <= now


def stay_reservation(stay: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """Normalize a manually supplied stay exactly like calendar reservations.

    Only timing and counts survive. Reservation IDs and labels never reach the
    AI provider because owners often put guest names in them.
    """
    if not stay.get("check_in"):
        return None
    normalized = normalize_reservations(
        [
            {
                "entity_id": STAY_ENTITY_ID,
                "attributes": {
                    "start": stay.get("check_in"),
                    "end": stay.get("check_out"),
                },
            }
        ],
        now,
    )
    if not normalized:
        return None
    item = normalized[0]
    item.pop("sequence", None)
    for key in ("guest_count", "pet_count"):
        if stay.get(key) is not None:
            item[key] = int(stay[key])
    return item


def resolve_occupancy(
    *,
    entity_state: str | None,
    stay: dict[str, Any] | None,
    reservations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Choose the most authoritative occupancy signal.

    Order: an explicitly configured occupancy entity, then a manually supplied
    stay, then reservation calendars.
    """
    if entity_state is not None:
        state = occupancy_from_state(entity_state)
        if state != "unknown":
            return {"state": state, "source": "occupancy_entity"}

    if stay is not None:
        if stay.get("status") == "current":
            return {"state": "occupied", "source": "stay_context"}
        return {"state": "vacant", "source": "stay_context"}

    if reservations:
        if any(item.get("status") == "current" for item in reservations):
            return {"state": "occupied", "source": "reservation_calendar"}
        return {"state": "vacant", "source": "reservation_calendar"}

    return {"state": "unknown", "source": None}


def next_arrival(
    stay: dict[str, Any] | None, reservations: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Return the soonest upcoming arrival, if any."""
    upcoming = [
        item
        for item in [*([stay] if stay else []), *reservations]
        if item.get("status") == "upcoming"
    ]
    if not upcoming:
        return None
    soonest = min(upcoming, key=lambda item: item.get("starts_in_minutes", 0))
    return {
        "start_local": soonest.get("start_local"),
        "relative_day": soonest.get("relative_day"),
        "starts_in_minutes": soonest.get("starts_in_minutes"),
    }
