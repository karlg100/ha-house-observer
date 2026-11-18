"""Load monitored-entity history and shape it for the house log."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import datetime
from functools import partial
from typing import Any

from homeassistant.core import HomeAssistant, State

from .houselog import UNAVAILABLE, Point, Series
from .models import ObservationEvent
from .reservations import is_reservation_state
from .semantics import semantic_state

_LOGGER = logging.getLogger(__name__)

CLIMATE_DOMAINS = {"climate", "water_heater"}
NUMERIC_DOMAINS = {"sensor", "number", "input_number", "counter"}
CLIMATE_ATTRIBUTES = (
    "hvac_action",
    "temperature",
    "target_temp_low",
    "target_temp_high",
)


def _domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def recorder_available(hass: HomeAssistant) -> bool:
    """Return whether the recorder can serve history."""
    return "recorder" in hass.config.components


async def async_recorder_points(
    hass: HomeAssistant,
    entity_ids: list[str],
    start: datetime,
    end: datetime,
) -> dict[str, list[Point]]:
    """Fetch every recorded state change from the recorder."""
    from homeassistant.components.recorder import get_instance, history  # noqa: PLC0415

    climate = [item for item in entity_ids if _domain(item) in CLIMATE_DOMAINS]
    plain = [item for item in entity_ids if _domain(item) not in CLIMATE_DOMAINS]
    recorder = get_instance(hass)
    output: dict[str, list[Point]] = {}
    for ids, with_attributes in ((plain, False), (climate, True)):
        if not ids:
            continue
        rows: dict[
            str, list[State | dict[str, Any]]
        ] = await recorder.async_add_executor_job(
            partial(
                history.get_significant_states,
                hass,
                start,
                end,
                entity_ids=ids,
                include_start_time_state=True,
                significant_changes_only=False,
                no_attributes=not with_attributes,
            )
        )
        for entity_id, states in rows.items():
            points: list[Point] = []
            for state in states:
                if not isinstance(state, State):
                    continue
                attributes = (
                    {
                        key: state.attributes[key]
                        for key in CLIMATE_ATTRIBUTES
                        if key in state.attributes
                    }
                    if with_attributes
                    else {}
                )
                points.append(
                    Point(max(state.last_updated, start), state.state, attributes)
                )
            output[entity_id] = points
    return output


def stored_event_points(
    events: Iterable[ObservationEvent], entity_ids: set[str], start: datetime
) -> dict[str, list[Point]]:
    """Fallback when the recorder is unavailable: use retained observations."""
    output: dict[str, list[Point]] = {}
    for event in events:
        when = event.when
        if when is None or when < start or event.entity_id not in entity_ids:
            continue
        output.setdefault(event.entity_id, []).append(
            Point(when, event.new_state, dict(event.attributes))
        )
    return output


def _kind(entity_id: str, points: list[Point]) -> str:
    domain = _domain(entity_id)
    if domain == "event":
        return "event"
    if domain in CLIMATE_DOMAINS:
        return "climate"
    if domain in NUMERIC_DOMAINS:
        values = [point.state for point in points if point.state not in UNAVAILABLE]
        try:
            if values:
                for value in values:
                    float(value)
                return "numeric"
        except ValueError:
            pass
    return "discrete"


def build_series(
    hass: HomeAssistant,
    raw: dict[str, list[Point]],
    *,
    category_for: Callable[[str], str],
    spread_for: Callable[[str], float | None],
    reservation_ids: set[str],
) -> list[Series]:
    """Attach names, categories, units, and semantic states."""
    output: list[Series] = []
    for entity_id, points in raw.items():
        if not points:
            continue
        current = hass.states.get(entity_id)
        attributes = dict(current.attributes) if current is not None else {}
        if is_reservation_state(entity_id, attributes, reservation_ids):
            continue  # booking calendars stay out of the log for privacy
        kind = _kind(entity_id, points)
        if kind == "discrete":
            points = [
                Point(
                    point.when,
                    point.state
                    if point.state in UNAVAILABLE
                    else semantic_state(entity_id, point.state, attributes),
                )
                for point in points
            ]
        output.append(
            Series(
                entity_id=entity_id,
                name=str(attributes.get("friendly_name") or entity_id),
                category=category_for(entity_id),
                kind=kind,
                points=sorted(points, key=lambda point: point.when),
                unit=attributes.get("unit_of_measurement"),
                spread=spread_for(entity_id) if kind == "numeric" else None,
            )
        )
    return output
