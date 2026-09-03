"""Privacy-safe, deterministic reservation context helpers."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

RESERVATION_CHANGED = "reservation_schedule_changed"


def _parse_datetime(value: Any, now: datetime) -> datetime | None:
    """Parse a Home Assistant datetime and express it in the local timezone."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    if now.tzinfo is not None:
        parsed = parsed.astimezone(now.tzinfo)
    return parsed


def is_reservation_state(
    entity_id: str, attributes: dict[str, Any], configured: set[str]
) -> bool:
    """Recognize configured reservation entities and calendar-like event sensors."""
    return entity_id in configured or ("start" in attributes and "end" in attributes)


def safe_reservation_attributes(attributes: dict[str, Any]) -> dict[str, str]:
    """Keep only validated schedule fields, excluding guest and booking details."""
    safe: dict[str, str] = {}
    for key in ("start", "end"):
        value = attributes.get(key)
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
        safe[key] = parsed.isoformat()
    return safe


def _relative_day(start: datetime, now: datetime) -> tuple[int, str]:
    """Return the calendar-day offset and an unambiguous label."""
    offset = (start.date() - now.date()).days
    if offset == -1:
        return offset, "yesterday"
    if offset == 0:
        return offset, "today"
    if offset == 1:
        return offset, "tomorrow"
    if offset > 1:
        return offset, f"in {offset} days"
    return offset, f"{abs(offset)} days ago"


def normalize_reservations(
    values: list[dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    """Sort reservation sensors and derive timing without exposing private data."""
    normalized: list[tuple[datetime, dict[str, Any]]] = []
    for value in values:
        attributes = value.get("attributes")
        if not isinstance(attributes, dict):
            continue
        start = _parse_datetime(attributes.get("start"), now)
        if start is None:
            continue
        end = _parse_datetime(attributes.get("end"), now)
        day_offset, relative_day = _relative_day(start, now)
        seconds_until_start = (start - now).total_seconds()

        if end is not None and start <= now < end:
            status = "current"
        elif start > now:
            status = "upcoming"
        else:
            status = "past"

        item: dict[str, Any] = {
            "entity_id": str(value.get("entity_id", "")),
            "status": status,
            "start_local": start.isoformat(),
            "relative_day": relative_day,
            "start_day_offset": day_offset,
        }
        if end is not None:
            item["end_local"] = end.isoformat()
        if status == "upcoming":
            item["starts_in_minutes"] = max(0, math.ceil(seconds_until_start / 60))
        else:
            item["started_minutes_ago"] = max(0, math.floor(-seconds_until_start / 60))
        if end is not None and end > now:
            item["ends_in_minutes"] = max(
                0, math.ceil((end - now).total_seconds() / 60)
            )
        normalized.append((start, item))

    normalized.sort(key=lambda entry: entry[0])
    result: list[dict[str, Any]] = []
    for sequence, (_, item) in enumerate(normalized, start=1):
        item["sequence"] = sequence
        result.append(item)
    return result
