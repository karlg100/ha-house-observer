"""Persistent observer memory and its one-time migrations."""

from __future__ import annotations

import secrets
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY_PREFIX, STORAGE_VERSION
from .patterns import numeric_value
from .reservations import RESERVATION_CHANGED, is_reservation_state
from .semantics import semantic_state

LEGACY_NOTE_ENTITY = "house_observer.note"


class ObserverStore(Store[dict[str, Any]]):
    """Store observer memory, migrating older layouts exactly once."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, reservation_entities: set[str]
    ) -> None:
        """Initialize the store."""
        super().__init__(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}{entry_id}",
            atomic_writes=True,
        )
        self._reservation_entities = reservation_entities

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict
    ) -> dict[str, Any]:
        """Migrate stored memory to the current layout."""
        data = dict(old_data)
        if old_major_version < 2:
            data = migrate_v1_to_v2(
                data,
                states=self.hass.states,
                reservation_entities=self._reservation_entities,
            )
        return data


def migrate_v1_to_v2(
    data: dict[str, Any], *, states: Any, reservation_entities: set[str]
) -> dict[str, Any]:
    """Move notes into knowledge and scrub data retained by early versions."""
    events: list[dict[str, Any]] = []
    knowledge: list[dict[str, Any]] = list(data.get("knowledge", []))
    patterns = data.get("patterns") or {"entities": {}}
    pattern_entities: dict[str, Any] = patterns.setdefault("entities", {})

    reservation_ids = set(reservation_entities)
    for entity_id in {event.get("entity_id") for event in data.get("events", [])} | set(
        pattern_entities
    ):
        state = states.get(entity_id) if entity_id else None
        if state is not None and is_reservation_state(
            entity_id, state.attributes, reservation_entities
        ):
            reservation_ids.add(entity_id)

    for event in data.get("events", []):
        event = dict(event)
        entity_id = event.get("entity_id")
        if entity_id == LEGACY_NOTE_ENTITY:
            knowledge.append(
                {
                    "note_id": secrets.token_hex(4),
                    "created": event.get("timestamp"),
                    "category": event.get("category", "owner"),
                    "text": event.get("new_state", ""),
                    "expires": None,
                }
            )
            continue
        if event.get("category") == "reservation" or entity_id in reservation_ids:
            event["category"] = "reservation"
            if event.get("old_state") is not None:
                event["old_state"] = RESERVATION_CHANGED
            event["new_state"] = RESERVATION_CHANGED
            event["attributes"] = {}
        else:
            attributes = event.get("attributes") or {}
            state = states.get(entity_id) if entity_id else None
            if state is not None and "device_class" not in attributes:
                attributes = state.attributes
            if event.get("old_state") is not None:
                event["old_state"] = semantic_state(
                    entity_id, event["old_state"], attributes
                )
            event["new_state"] = semantic_state(
                entity_id, event.get("new_state", "unknown"), attributes
            )
        events.append(event)

    for entity_id, record in pattern_entities.items():
        counts: dict[str, int] = dict(record.get("state_counts", {}))
        if entity_id in reservation_ids:
            total = sum(int(value) for value in counts.values())
            record["state_counts"] = {RESERVATION_CHANGED: total} if total else {}
            continue
        if int((record.get("numeric") or {}).get("count", 0)):
            counts = {
                key: value
                for key, value in counts.items()
                if numeric_value(key) is None
            }
        state = states.get(entity_id)
        if state is not None and entity_id.startswith("binary_sensor."):
            normalized: dict[str, int] = {}
            for value, count in counts.items():
                label = semantic_state(entity_id, value, state.attributes)
                normalized[label] = normalized.get(label, 0) + int(count)
            counts = normalized
            if last_state := record.get("last_state"):
                record["last_state"] = semantic_state(
                    entity_id, last_state, state.attributes
                )
        record["state_counts"] = counts

    data["events"] = events
    data["knowledge"] = knowledge
    data["patterns"] = patterns
    return data
