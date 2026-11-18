"""Tools that let a conversation agent read House Observer telemetry.

Enable "House Observer" in a conversation agent's settings to give it these
tools. They are read-only and limited to each property's monitored entities.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from homeassistant.util.json import JsonObjectType

from .const import DOMAIN, LLM_API_ID
from .houselog import UNAVAILABLE
from .ingest import minutes_since
from .knowledge import knowledge_context, previous_summaries
from .semantics import semantic_state
from .telemetry import async_recorder_points, recorder_available, stored_event_points

if TYPE_CHECKING:
    from .observer import HouseObserver

DATA_MANAGERS = "managers"
MAX_RANGE = timedelta(days=7)
MAX_READINGS = 1000

_PROPERTY = vol.Optional(
    "property",
    description="Property name; required only when several are configured.",
)
_RANGE = {
    vol.Optional(
        "hours",
        description="Hours back from now (default 24); ignored when start is set.",
    ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=168)),
    vol.Optional("start", description="Range start as a local ISO date-time."): str,
    vol.Optional("end", description="Range end as a local ISO date-time."): str,
}


def _observers(hass: HomeAssistant) -> dict[str, HouseObserver]:
    return dict(hass.data.get(DOMAIN, {}).get(DATA_MANAGERS, {}))


def _observer(hass: HomeAssistant, args: dict[str, Any]) -> HouseObserver:
    observers = _observers(hass)
    if not observers:
        raise HomeAssistantError("No House Observer property is loaded")
    if name := str(args.get("property") or "").strip().lower():
        for observer in observers.values():
            if name in {
                observer.property_name.lower(),
                observer.entry.entry_id.lower(),
            }:
                return observer
        raise HomeAssistantError(f"No House Observer property named {name}")
    if len(observers) == 1:
        return next(iter(observers.values()))
    names = ", ".join(observer.property_name for observer in observers.values())
    raise HomeAssistantError(f"Specify property; configured properties: {names}")


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = dt_util.parse_datetime(str(value))
    if parsed is None:
        raise HomeAssistantError(f"Could not read the date-time {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return parsed


def _range(args: dict[str, Any]) -> tuple[datetime, datetime]:
    end = _parse(args.get("end")) or dt_util.now()
    start = _parse(args.get("start")) or end - timedelta(
        hours=float(args.get("hours") or 24)
    )
    if start >= end:
        raise HomeAssistantError("start must be before end")
    if end - start > MAX_RANGE:
        start = end - MAX_RANGE
    return start, end


class ListEntitiesTool(llm.Tool):
    """List monitored entities with their current state."""

    name = "house_observer_list_entities"
    description = (
        "List the entities House Observer monitors for a property, with "
        "category, current state, and minutes in that state."
    )
    parameters = vol.Schema({_PROPERTY: str})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        observer = _observer(hass, tool_input.tool_args)
        now = dt_util.now()
        occupancy, _, _ = observer.occupancy_context(now)
        entities = []
        for entity_id in observer.tracked_entities:
            state = hass.states.get(entity_id)
            if state is None:
                continue
            entities.append(
                {
                    "entity_id": entity_id,
                    "name": state.name,
                    "category": observer.category_for(entity_id),
                    "state": semantic_state(entity_id, state.state, state.attributes),
                    "unit": state.attributes.get("unit_of_measurement"),
                    "for_minutes": minutes_since(state.last_changed, now),
                }
            )
        return {
            "property": observer.property_name,
            "mode": observer.mode,
            "occupancy": occupancy["state"],
            "entities": entities,
        }


class GetLogTool(llm.Tool):
    """Render the compact house log for any range up to seven days."""

    name = "house_observer_get_log"
    description = (
        "Return the compact house log: every door, lock, appliance, and "
        "occupancy change as intervals, numeric sensors as change-point "
        "segments, and thermostat changes, for a time range up to 7 days. "
        "Optionally limit it to some entity_ids."
    )
    parameters = vol.Schema(
        {
            _PROPERTY: str,
            **_RANGE,
            vol.Optional(
                "entity_ids", description="Only these monitored entity IDs."
            ): [str],
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        observer = _observer(hass, tool_input.tool_args)
        start, end = _range(tool_input.tool_args)
        tracked = set(observer.tracked_entities)
        wanted = tool_input.tool_args.get("entity_ids")
        ids = [item for item in (wanted or tracked) if item in tracked]
        if wanted and not ids:
            raise HomeAssistantError("None of those entities are monitored")
        log = await observer.async_house_log(start, end, entity_ids=ids)
        return {
            "property": observer.property_name,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "log": log.text if log else "No recorded changes in this range.",
        }


class GetReadingsTool(llm.Tool):
    """Return raw recorded readings for one monitored entity."""

    name = "house_observer_get_readings"
    description = (
        "Return the raw recorded states of one monitored entity for a time "
        "range up to 7 days, as [local time, state] pairs. Use it to inspect "
        "a spike, a short open/close, or a value the house log summarized."
    )
    parameters = vol.Schema(
        {
            _PROPERTY: str,
            vol.Required("entity_id", description="A monitored entity ID."): str,
            **_RANGE,
            vol.Optional(
                "max_points", description="Most readings to return (default 300)."
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=MAX_READINGS)),
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        args = tool_input.tool_args
        observer = _observer(hass, args)
        entity_id = str(args["entity_id"])
        if entity_id not in observer.tracked_entities:
            raise HomeAssistantError(f"{entity_id} is not a monitored entity")
        start, end = _range(args)
        if recorder_available(hass):
            raw = await async_recorder_points(hass, [entity_id], start, end)
        else:
            raw = stored_event_points(observer.events, {entity_id}, start)
        points = [point for point in raw.get(entity_id, []) if point.when <= end]
        limit = int(args.get("max_points") or 300)
        sampled = _downsample(points, limit)
        state = hass.states.get(entity_id)
        attributes = state.attributes if state is not None else {}
        return {
            "entity_id": entity_id,
            "unit": attributes.get("unit_of_measurement"),
            "total_readings": len(points),
            "returned_readings": len(sampled),
            "downsampled": len(sampled) < len(points),
            "readings": [
                [
                    dt_util.as_local(point.when).strftime("%Y-%m-%d %H:%M:%S"),
                    point.state
                    if point.state in UNAVAILABLE
                    else semantic_state(entity_id, point.state, attributes),
                ]
                for point in sampled
            ],
        }


def _downsample(points: list[Any], limit: int) -> list[Any]:
    """Keep evenly spaced readings plus the minimum and maximum."""
    if len(points) <= limit:
        return points
    step = len(points) / limit
    keep = {int(index * step) for index in range(limit)}
    numeric = []
    for index, point in enumerate(points):
        try:
            numeric.append((float(point.state), index))
        except ValueError:
            continue
    if numeric:
        keep.add(min(numeric)[1])
        keep.add(max(numeric)[1])
    return [points[index] for index in sorted(keep)]


class GetContextTool(llm.Tool):
    """Return recent summaries and saved property notes."""

    name = "house_observer_get_context"
    description = (
        "Return recent House Observer summaries and the owner's saved property "
        "notes and confirmed patterns."
    )
    parameters = vol.Schema(
        {
            _PROPERTY: str,
            vol.Optional(
                "summaries", description="How many recent summaries (default 5)."
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        observer = _observer(hass, tool_input.tool_args)
        limit = int(tool_input.tool_args.get("summaries") or 5)
        return {
            "property": observer.property_name,
            "summaries": previous_summaries(observer.summaries, limit),
            "property_knowledge": knowledge_context(observer.knowledge),
        }


class HouseObserverAPI(llm.API):
    """Read-only House Observer tools."""

    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        """Return the tools for one request."""
        names = ", ".join(
            observer.property_name for observer in _observers(self.hass).values()
        )
        return llm.APIInstance(
            api=self,
            api_prompt=(
                "House Observer tools give read-only access to the monitored "
                f"telemetry of: {names or 'no loaded properties'}. Times are "
                "local. Use them to answer questions about what happened at a "
                "property, and to inspect detail behind a summary."
            ),
            llm_context=llm_context,
            tools=[
                ListEntitiesTool(),
                GetLogTool(),
                GetReadingsTool(),
                GetContextTool(),
            ],
        )


def async_register(hass: HomeAssistant) -> None:
    """Register the API once per Home Assistant run."""
    if any(api.id == LLM_API_ID for api in llm.async_get_apis(hass)):
        return
    llm.async_register_api(
        hass, HouseObserverAPI(hass=hass, id=LLM_API_ID, name="House Observer")
    )
