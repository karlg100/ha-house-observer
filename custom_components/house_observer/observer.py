"""Event collection, local memory, summaries, and anomaly handling."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_FRIENDLY_NAME,
    ATTR_UNIT_OF_MEASUREMENT,
    EVENT_HOMEASSISTANT_STOP,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_ALWAYS_MONITOR_DEVICES,
    CONF_ALWAYS_MONITOR_ENTITIES,
    CONF_AUTO_DISCOVERY,
    CONF_DISCOVERY_INTERVAL_HOURS,
    CONF_LEARNING_ONLY,
    CONF_LOOKBACK_HOURS,
    CONF_MIN_SAMPLES,
    CONF_NEVER_MONITOR_DEVICES,
    CONF_NEVER_MONITOR_ENTITIES,
    CONF_NOTIFY_DAILY,
    CONF_NOTIFY_SERVICE,
    CONF_PROPERTY_NAME,
    CONF_RETENTION_DAYS,
    CONF_SUMMARY_TIME,
    CONF_ZSCORE_THRESHOLD,
    DEFAULT_ANOMALY_COOLDOWN_MINUTES,
    DEFAULT_AUTO_DISCOVERY,
    DEFAULT_DISCOVERY_INTERVAL_HOURS,
    DEFAULT_EVENT_DEBOUNCE_SECONDS,
    DEFAULT_LEARNING_ONLY,
    DEFAULT_LOOKBACK_HOURS,
    DEFAULT_MAX_EVENTS,
    DEFAULT_MAX_SUMMARIES,
    DEFAULT_MIN_SAMPLES,
    DEFAULT_NOTIFY_DAILY,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_SUMMARY_TIME,
    DEFAULT_ZSCORE_THRESHOLD,
    DOMAIN,
    ENTITY_GROUPS,
    GROUP_LABELS,
    SEVERITIES,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)
from .discovery import (
    DiscoveryCandidate,
    default_category,
    eligible_candidates,
    fallback_recommendations,
    parse_recommendations,
)
from .models import ObservationEvent, ObserverSummary
from .patterns import PatternEngine, numeric_value

_LOGGER = logging.getLogger(__name__)

_SAFE_ATTRIBUTES = (
    "battery_level",
    "current_temperature",
    "device_class",
    "hvac_action",
    "hvac_mode",
    "mode",
    "percentage",
    "preset_mode",
    "signal_strength",
    "temperature",
)

_AI_STRUCTURE: dict[str, Any] = {
    "summary": {
        "description": "A concise plain-language operational summary.",
        "required": True,
        "selector": {"text": {"multiline": True}},
    },
    "severity": {
        "description": "Overall operational severity.",
        "required": True,
        "selector": {"select": {"options": list(SEVERITIES)}},
    },
    "confidence": {
        "description": "Confidence from zero to one.",
        "required": True,
        "selector": {"number": {"min": 0, "max": 1, "step": 0.01}},
    },
    "observations": {
        "description": "Important observations, one per line.",
        "selector": {"text": {"multiline": True}},
    },
    "anomalies": {
        "description": "Possible anomalies, one per line; blank if none.",
        "selector": {"text": {"multiline": True}},
    },
    "maintenance_notes": {
        "description": "Possible maintenance items, one per line; blank if none.",
        "selector": {"text": {"multiline": True}},
    },
    "candidate_memories": {
        "description": (
            "Reusable property patterns supported by evidence, one per line."
        ),
        "selector": {"text": {"multiline": True}},
    },
    "notify_owner": {
        "description": "True only when timely owner attention is justified.",
        "required": True,
        "selector": {"boolean": {}},
    },
}

_DISCOVERY_AI_STRUCTURE: dict[str, Any] = {
    "recommendations": {
        "description": (
            "One selected entity per line as entity_id | category | concise reason."
        ),
        "required": True,
        "selector": {"text": {"multiline": True}},
    },
    "confidence": {
        "description": "Overall confidence from zero to one.",
        "required": True,
        "selector": {"number": {"min": 0, "max": 1, "step": 0.01}},
    },
}


def _as_bool(value: Any) -> bool:
    """Normalize model-returned booleans."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_confidence(value: Any) -> float:
    """Normalize a confidence value to zero through one."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _limited_text(value: Any, limit: int = 4000) -> str:
    """Bound model and owner text stored in entity attributes."""
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


class HouseObserver:
    """Observe selected Home Assistant entities for one property."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize an observer."""
        self.hass = hass
        self.entry = entry
        self.events: list[ObservationEvent] = []
        self.summaries: list[ObserverSummary] = []
        self.stay_context: dict[str, Any] = {}
        self.patterns = PatternEngine()
        self.created_at = dt_util.now().isoformat()
        self.last_anomaly_alert: datetime | None = None
        self.discovery: dict[str, Any] = {}
        self.discovery_activity: dict[str, int] = {}
        self._discovery_candidates: dict[str, DiscoveryCandidate] = {}
        self._entity_devices: dict[str, str | None] = {}
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}{entry.entry_id}",
        )
        self._unsubscribers: list[Callable[[], None]] = []
        self._observation_unsubscriber: Callable[[], None] | None = None
        self._candidate_unsubscriber: Callable[[], None] | None = None
        self._listeners: set[Callable[[], None]] = set()
        self._last_numeric_event: dict[str, tuple[datetime, float]] = {}
        self._lock = asyncio.Lock()
        self._discovery_lock = asyncio.Lock()

    @property
    def property_name(self) -> str:
        """Return the configured property name."""
        return str(self.entry.data[CONF_PROPERTY_NAME])

    @property
    def options(self) -> dict[str, Any]:
        """Return options with defaults filled in."""
        return {
            CONF_LOOKBACK_HOURS: DEFAULT_LOOKBACK_HOURS,
            CONF_RETENTION_DAYS: DEFAULT_RETENTION_DAYS,
            CONF_LEARNING_ONLY: DEFAULT_LEARNING_ONLY,
            CONF_NOTIFY_DAILY: DEFAULT_NOTIFY_DAILY,
            CONF_MIN_SAMPLES: DEFAULT_MIN_SAMPLES,
            CONF_ZSCORE_THRESHOLD: DEFAULT_ZSCORE_THRESHOLD,
            CONF_SUMMARY_TIME: DEFAULT_SUMMARY_TIME,
            CONF_AUTO_DISCOVERY: DEFAULT_AUTO_DISCOVERY,
            CONF_DISCOVERY_INTERVAL_HOURS: DEFAULT_DISCOVERY_INTERVAL_HOURS,
            **self.entry.options,
        }

    @property
    def tracked_entities(self) -> list[str]:
        """Return manual, discovered, and user-forced entity IDs."""
        entities: set[str] = set()
        for group in ENTITY_GROUPS:
            entities.update(self.options.get(group, []))

        if self.options[CONF_AUTO_DISCOVERY]:
            entities.update(
                item["entity_id"]
                for item in self.discovery.get("recommendations", [])
                if item.get("entity_id")
            )
        entities.update(self.options.get(CONF_ALWAYS_MONITOR_ENTITIES, []))

        always_devices = set(self.options.get(CONF_ALWAYS_MONITOR_DEVICES, []))
        never_devices = set(self.options.get(CONF_NEVER_MONITOR_DEVICES, []))
        for candidate in self._discovery_candidates.values():
            if candidate.device_id in always_devices:
                entities.add(candidate.entity_id)

        entities.difference_update(self.options.get(CONF_NEVER_MONITOR_ENTITIES, []))
        entities = {
            entity_id
            for entity_id in entities
            if self._entity_devices.get(entity_id) not in never_devices
        }
        return sorted(entities)

    @property
    def learning_only(self) -> bool:
        """Return whether proactive notifications are suppressed."""
        return bool(self.options[CONF_LEARNING_ONLY])

    async def async_start(self) -> None:
        """Load memory and begin observing."""
        stored = await self._store.async_load() or {}
        self.events = [
            ObservationEvent.from_dict(value) for value in stored.get("events", [])
        ]
        self.summaries = [
            ObserverSummary.from_dict(value) for value in stored.get("summaries", [])
        ]
        self.stay_context = dict(stored.get("stay_context", {}))
        self.discovery = dict(stored.get("discovery", {}))
        self.discovery_activity = {
            str(key): int(value)
            for key, value in stored.get("discovery_activity", {}).items()
        }
        self.created_at = str(stored.get("created_at", self.created_at))
        self.patterns = PatternEngine(
            stored.get("patterns"),
            minimum_samples=int(self.options[CONF_MIN_SAMPLES]),
            zscore_threshold=float(self.options[CONF_ZSCORE_THRESHOLD]),
        )
        if value := stored.get("last_anomaly_alert"):
            self.last_anomaly_alert = dt_util.parse_datetime(str(value))

        self._prune()
        self._refresh_discovery_candidates()
        self._rebind_observation_listener()
        if self.options[CONF_AUTO_DISCOVERY]:
            if self._discovery_candidates:
                self._candidate_unsubscriber = async_track_state_change_event(
                    self.hass,
                    list(self._discovery_candidates),
                    self._async_discovery_candidate_changed,
                )
            self._unsubscribers.append(
                async_track_time_interval(
                    self.hass,
                    self._async_discovery_check,
                    timedelta(hours=24),
                )
            )
            if self._discovery_is_due():
                self._unsubscribers.append(
                    async_call_later(self.hass, 15, self._async_discovery_check)
                )
        hour, minute, second = self._summary_time_parts()
        self._unsubscribers.append(
            async_track_time_change(
                self.hass,
                self._async_daily_summary,
                hour=hour,
                minute=minute,
                second=second,
            )
        )
        self._unsubscribers.append(
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, self._async_home_assistant_stop
            )
        )
        self._notify_listeners()

    async def async_stop(self) -> None:
        """Stop observing and persist immediately."""
        if self._observation_unsubscriber:
            self._observation_unsubscriber()
            self._observation_unsubscriber = None
        if self._candidate_unsubscriber:
            self._candidate_unsubscriber()
            self._candidate_unsubscriber = None
        while self._unsubscribers:
            self._unsubscribers.pop()()
        await self._store.async_save(self._storage_payload())

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register an entity update listener."""
        self._listeners.add(listener)

        @callback
        def remove_listener() -> None:
            self._listeners.discard(listener)

        return remove_listener

    @callback
    def _notify_listeners(self) -> None:
        """Notify observer entities of new data."""
        for listener in self._listeners:
            listener()

    def _refresh_discovery_candidates(self) -> None:
        """Build a privacy-conscious inventory from Home Assistant registries."""
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)
        area_registry = ar.async_get(self.hass)
        candidates: list[DiscoveryCandidate] = []
        self._entity_devices = {}

        for entry in entity_registry.entities.values():
            if (
                entry.disabled_by is not None
                or entry.hidden_by is not None
                or entry.platform == DOMAIN
            ):
                continue
            state = self.hass.states.get(entry.entity_id)
            if state is None:
                continue
            device = (
                device_registry.async_get(entry.device_id) if entry.device_id else None
            )
            if device is not None and device.disabled_by is not None:
                continue
            self._entity_devices[entry.entity_id] = entry.device_id
            area_id = entry.area_id or (device.area_id if device else None)
            area = area_registry.async_get_area(area_id) if area_id else None
            category = getattr(entry.entity_category, "value", entry.entity_category)
            device_class = state.attributes.get("device_class") or getattr(
                entry, "original_device_class", None
            )
            candidate = DiscoveryCandidate(
                entity_id=entry.entity_id,
                name=state.name,
                state=state.state,
                domain=entry.entity_id.split(".", 1)[0],
                device_class=str(device_class) if device_class else None,
                unit=state.attributes.get(ATTR_UNIT_OF_MEASUREMENT),
                entity_category=str(category) if category else None,
                device_id=entry.device_id,
                device_name=(
                    str(device.name_by_user or device.name) if device else None
                ),
                area_id=area_id,
                area_name=str(area.name) if area else "Unassigned",
                activity_count=int(self.discovery_activity.get(entry.entity_id, 0)),
            )
            candidates.append(candidate)

        self._discovery_candidates = {
            item.entity_id: item for item in eligible_candidates(candidates)
        }

    @callback
    def _rebind_observation_listener(self) -> None:
        """Listen only to the currently effective monitored entity set."""
        if self._observation_unsubscriber:
            self._observation_unsubscriber()
            self._observation_unsubscriber = None
        if self.tracked_entities:
            self._observation_unsubscriber = async_track_state_change_event(
                self.hass,
                self.tracked_entities,
                self._async_state_changed,
            )

    @callback
    def _async_discovery_candidate_changed(self, event: Event) -> None:
        """Learn which eligible entities actually change, without retaining states."""
        entity_id = str(event.data.get("entity_id", ""))
        old_state: State | None = event.data.get("old_state")
        new_state: State | None = event.data.get("new_state")
        if not entity_id or new_state is None:
            return
        if old_state is not None and old_state.state == new_state.state:
            return
        self.discovery_activity[entity_id] = min(
            1_000_000, int(self.discovery_activity.get(entity_id, 0)) + 1
        )
        if candidate := self._discovery_candidates.get(entity_id):
            candidate.activity_count = self.discovery_activity[entity_id]
        self._schedule_save()

    def _discovery_is_due(self) -> bool:
        """Return whether automatic discovery should be refreshed."""
        last_run = dt_util.parse_datetime(str(self.discovery.get("last_run", "")))
        if last_run is None:
            return True
        interval = timedelta(hours=int(self.options[CONF_DISCOVERY_INTERVAL_HOURS]))
        if not self.discovery.get("ai_generated"):
            interval = min(interval, timedelta(hours=24))
        return dt_util.now() - last_run >= interval

    @callback
    def _async_discovery_check(self, now: datetime) -> None:
        """Schedule discovery when its learning interval has elapsed."""
        if not self.options[CONF_AUTO_DISCOVERY] or not self._discovery_is_due():
            return
        self.entry.async_create_task(
            self.hass,
            self.async_discover_entities(reason="scheduled"),
            f"Discover important {self.property_name} entities",
        )

    async def async_discover_entities(self, *, reason: str) -> dict[str, Any]:
        """Ask the configured AI to choose a minimal operational entity set."""
        async with self._discovery_lock:
            return await self._async_discover_entities_locked(reason)

    async def _async_discover_entities_locked(self, reason: str) -> dict[str, Any]:
        """Run one serialized entity discovery operation."""
        self._refresh_discovery_candidates()
        candidates = list(self._discovery_candidates.values())
        recommendations: list[dict[str, Any]] = []
        ai_generated = False

        if candidates and self.hass.services.has_service("ai_task", "generate_data"):
            by_area: dict[str, list[dict[str, Any]]] = {}
            for candidate in candidates:
                by_area.setdefault(candidate.area_name, []).append(
                    candidate.prompt_dict()
                )
            service_data: dict[str, Any] = {
                "task_name": f"{self.property_name} entity discovery",
                "instructions": self._build_discovery_prompt(by_area),
                "structure": _DISCOVERY_AI_STRUCTURE,
            }
            if ai_entity := self.options.get(CONF_AI_TASK_ENTITY):
                service_data["entity_id"] = ai_entity
            try:
                response = await self.hass.services.async_call(
                    "ai_task",
                    "generate_data",
                    service_data,
                    blocking=True,
                    return_response=True,
                )
                data = response.get("data") if isinstance(response, dict) else None
                if isinstance(data, dict):
                    recommendations = parse_recommendations(
                        data.get("recommendations"),
                        candidates,
                        confidence=data.get("confidence", 0),
                    )
                    ai_generated = bool(recommendations)
            except (HomeAssistantError, TimeoutError, ValueError):
                _LOGGER.exception("Unable to run House Observer entity discovery")

        if not recommendations:
            recommendations = fallback_recommendations(candidates)

        self.discovery = {
            "last_run": dt_util.now().isoformat(),
            "reason": reason,
            "ai_generated": ai_generated,
            "candidate_count": len(candidates),
            "recommendations": recommendations,
        }
        self.discovery_activity = {
            entity_id: 0 for entity_id in self.discovery_activity
        }
        self._rebind_observation_listener()
        self._schedule_save()
        self._notify_listeners()
        response = self.discovery_response()
        self.hass.bus.async_fire("house_observer_discovery_completed", response)
        return response

    def discovery_response(self) -> dict[str, Any]:
        """Return a bounded, user-facing discovery result."""
        recommendations = self.discovery.get("recommendations", [])
        device_ids = {item.get("device_id") for item in recommendations}
        device_ids.discard(None)
        return {
            "last_run": self.discovery.get("last_run"),
            "reason": self.discovery.get("reason"),
            "ai_generated": bool(self.discovery.get("ai_generated")),
            "candidate_count": int(self.discovery.get("candidate_count", 0)),
            "recommended_device_count": len(device_ids),
            "recommended_entity_count": len(recommendations),
            "tracked_entity_count": len(self.tracked_entities),
            "recommendations": recommendations[:100],
        }

    def _build_discovery_prompt(
        self, inventory_by_area: dict[str, list[dict[str, Any]]]
    ) -> str:
        """Build the area-aware device and entity selection prompt."""
        return (
            "You are selecting Home Assistant telemetry for a concise operational "
            "house summary. Review every supplied area, but choose the smallest useful "
            "set. Prefer exterior access, meaningful activity or occupancy indicators, "
            "HVAC and room comfort, water or leak conditions, Internet availability, "
            "important energy loads, and operational equipment. Avoid redundant "
            "entities from the same device, noisy controls, configuration entities, "
            "and values that would not change a house summary. Recent_changes is a "
            "local activity count and local_relevance is a preliminary non-AI hint, "
            "not an instruction. Do not infer identities, motives, or surveillance. "
            "Use only entity IDs in the inventory. Output exactly one line per "
            "selected entity as: entity_id | category | concise reason. Allowed "
            "categories: "
            "access, activity, occupancy, spa, hvac, network, energy, reservation, "
            "other. It is valid to select nothing from an area.\n\n"
            "Area inventory JSON:\n"
            f"{json.dumps(inventory_by_area, separators=(',', ':'), default=str)}"
        )

    @callback
    def _async_state_changed(self, event: Event) -> None:
        """Schedule processing for a state-change event."""
        self.entry.async_create_task(
            self.hass,
            self._async_process_state_change(event),
            f"Process {self.property_name} observation",
        )

    async def _async_process_state_change(self, event: Event) -> None:
        """Convert a raw state change into a bounded observation."""
        old_state: State | None = event.data.get("old_state")
        new_state: State | None = event.data.get("new_state")
        if new_state is None or new_state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return
        if old_state is not None and old_state.state == new_state.state:
            return

        observed_at = dt_util.as_local(new_state.last_updated)
        numeric = numeric_value(new_state.state)
        if numeric is not None and self._debounce_numeric(
            new_state.entity_id, observed_at, numeric
        ):
            return

        async with self._lock:
            anomaly = self.patterns.observe(
                new_state.entity_id, new_state.state, observed_at
            )
            observation = ObservationEvent(
                timestamp=observed_at.isoformat(),
                entity_id=new_state.entity_id,
                name=str(new_state.attributes.get(ATTR_FRIENDLY_NAME, new_state.name)),
                category=self._category_for(new_state.entity_id),
                old_state=old_state.state if old_state else None,
                new_state=new_state.state,
                unit=new_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT),
                attributes=self._safe_attributes(new_state),
                anomaly=anomaly,
            )
            self.events.append(observation)
            self._prune()
            self._schedule_save()
            self._notify_listeners()

        if anomaly and not self.learning_only and self._anomaly_alert_due(observed_at):
            self.last_anomaly_alert = observed_at
            self.entry.async_create_task(
                self.hass,
                self.async_generate_summary(reason="anomaly", hours=2),
                f"Generate {self.property_name} anomaly summary",
            )

    def _debounce_numeric(
        self, entity_id: str, observed_at: datetime, value: float
    ) -> bool:
        """Avoid turning high-frequency numeric telemetry into event spam."""
        previous = self._last_numeric_event.get(entity_id)
        if previous is not None:
            previous_time, _ = previous
            if (
                observed_at - previous_time
            ).total_seconds() < DEFAULT_EVENT_DEBOUNCE_SECONDS:
                return True
        self._last_numeric_event[entity_id] = (observed_at, value)
        return False

    def _anomaly_alert_due(self, now: datetime) -> bool:
        """Rate limit proactive anomaly analysis."""
        if self.last_anomaly_alert is None:
            return True
        return now - self.last_anomaly_alert >= timedelta(
            minutes=DEFAULT_ANOMALY_COOLDOWN_MINUTES
        )

    @callback
    def _async_daily_summary(self, now: datetime) -> None:
        """Schedule the daily summary."""
        self.entry.async_create_task(
            self.hass,
            self.async_generate_summary(
                reason="daily",
                hours=int(self.options[CONF_LOOKBACK_HOURS]),
            ),
            f"Generate {self.property_name} daily summary",
        )

    async def _async_home_assistant_stop(self, event: Event) -> None:
        """Persist data during Home Assistant shutdown."""
        await self._store.async_save(self._storage_payload())

    async def async_generate_summary(
        self, *, reason: str = "manual", hours: int | None = None
    ) -> ObserverSummary:
        """Generate and store an operational summary."""
        period_hours = hours or int(self.options[CONF_LOOKBACK_HOURS])
        context = self._build_context(period_hours)
        result: dict[str, Any] | None = None
        ai_generated = False

        if self.hass.services.has_service("ai_task", "generate_data"):
            service_data: dict[str, Any] = {
                "task_name": f"{self.property_name} house observer summary",
                "instructions": self._build_prompt(context, reason),
                "structure": _AI_STRUCTURE,
            }
            if ai_entity := self.options.get(CONF_AI_TASK_ENTITY):
                service_data["entity_id"] = ai_entity
            try:
                response = await self.hass.services.async_call(
                    "ai_task",
                    "generate_data",
                    service_data,
                    blocking=True,
                    return_response=True,
                )
                if isinstance(response, dict) and isinstance(
                    response.get("data"), dict
                ):
                    result = response["data"]
                    ai_generated = True
            except (HomeAssistantError, TimeoutError, ValueError):
                _LOGGER.exception("Unable to generate House Observer AI summary")

        if result is None:
            result = self._fallback_summary(context)

        severity = str(result.get("severity", "normal")).lower()
        if severity not in SEVERITIES:
            severity = "note"
        summary = ObserverSummary(
            timestamp=dt_util.now().isoformat(),
            reason=reason,
            period_hours=period_hours,
            summary=_limited_text(
                result.get("summary", "No summary was generated."), 2000
            ),
            severity=severity,
            confidence=_as_confidence(result.get("confidence")),
            observations=_limited_text(result.get("observations", "")),
            anomalies=_limited_text(result.get("anomalies", "")),
            maintenance_notes=_limited_text(result.get("maintenance_notes", "")),
            candidate_memories=_limited_text(result.get("candidate_memories", "")),
            notify_owner=_as_bool(result.get("notify_owner", False)),
            ai_generated=ai_generated,
        )
        self.summaries.append(summary)
        self.summaries = self.summaries[-DEFAULT_MAX_SUMMARIES:]
        self._schedule_save()
        self._notify_listeners()
        self.hass.bus.async_fire("house_observer_summary_generated", summary.as_dict())
        await self._async_maybe_notify(summary)
        return summary

    async def async_record_note(self, note: str, category: str = "owner") -> None:
        """Add a durable owner or maintenance note to the event stream."""
        now = dt_util.now()
        self.events.append(
            ObservationEvent(
                timestamp=now.isoformat(),
                entity_id="house_observer.note",
                name="House note",
                category=category,
                old_state=None,
                new_state=_limited_text(note),
            )
        )
        self._prune()
        self._schedule_save()
        self._notify_listeners()

    async def async_set_stay_context(self, context: dict[str, Any]) -> None:
        """Set or clear current reservation context."""
        self.stay_context = context
        self._schedule_save()
        self._notify_listeners()

    def _category_for(self, entity_id: str) -> str:
        """Return the configured semantic category for an entity."""
        for group in ENTITY_GROUPS:
            if entity_id in self.options.get(group, []):
                return GROUP_LABELS[group]
        for item in self.discovery.get("recommendations", []):
            if item.get("entity_id") == entity_id:
                return str(item.get("category", "other"))
        if candidate := self._discovery_candidates.get(entity_id):
            return default_category(candidate)
        return "other"

    @staticmethod
    def _safe_attributes(state: State) -> dict[str, Any]:
        """Keep a deliberate small subset of useful state attributes."""
        return {
            key: state.attributes[key]
            for key in _SAFE_ATTRIBUTES
            if key in state.attributes
            and isinstance(state.attributes[key], (str, int, float, bool, type(None)))
        }

    def _summary_time_parts(self) -> tuple[int, int, int]:
        """Parse the configured local summary time."""
        raw = self.options.get(CONF_SUMMARY_TIME, DEFAULT_SUMMARY_TIME)
        if hasattr(raw, "hour"):
            return int(raw.hour), int(raw.minute), int(raw.second)
        try:
            parts = [int(value) for value in str(raw).split(":")]
            return parts[0], parts[1], parts[2] if len(parts) > 2 else 0
        except (TypeError, ValueError, IndexError):
            return 8, 0, 0

    def events_since(self, hours: int) -> list[ObservationEvent]:
        """Return recent events."""
        cutoff = dt_util.now() - timedelta(hours=hours)
        return [
            event
            for event in self.events
            if (timestamp := dt_util.parse_datetime(event.timestamp)) is not None
            and timestamp >= cutoff
        ]

    def _build_context(self, hours: int) -> dict[str, Any]:
        """Build a compact context object for the model."""
        recent = self.events_since(hours)
        category_counts: dict[str, int] = {}
        anomaly_count = 0
        for event in recent:
            category_counts[event.category] = category_counts.get(event.category, 0) + 1
            anomaly_count += event.anomaly is not None

        current_states: list[dict[str, Any]] = []
        for entity_id in self.tracked_entities:
            if state := self.hass.states.get(entity_id):
                current_states.append(
                    {
                        "entity_id": entity_id,
                        "name": state.name,
                        "category": self._category_for(entity_id),
                        "state": state.state,
                        "unit": state.attributes.get(ATTR_UNIT_OF_MEASUREMENT),
                        "attributes": self._safe_attributes(state),
                    }
                )

        event_payload = [event.as_dict() for event in recent[-250:]]
        return {
            "property": self.property_name,
            "generated_at": dt_util.now().isoformat(),
            "period_hours": hours,
            "learning_only": self.learning_only,
            "stay_context": self.stay_context,
            "event_count": len(recent),
            "omitted_older_events": max(0, len(recent) - len(event_payload)),
            "category_counts": category_counts,
            "anomaly_count": anomaly_count,
            "current_states": current_states,
            "recent_events": event_payload,
            "learned_baselines": self.patterns.baseline_context(),
        }

    def _build_prompt(self, context: dict[str, Any], reason: str) -> str:
        """Build the operational-analysis prompt."""
        return (
            "You are the operational observer for a short-term rental property. "
            "Analyze only the supplied Home Assistant telemetry and property context. "
            "Do not invent events, identities, occupancy counts, motives, diagnoses, "
            "or guest characteristics. Phrase uncertain occupancy conclusions as "
            "apparent activity. Compare telemetry to baselines only when enough "
            "evidence exists. Routine behavior is normal, not suspicious. A numeric "
            "deviation "
            "is a clue, not proof of a fault. Safety automations remain authoritative; "
            "you are only "
            "providing analysis. Use ACTION for a timely condition that probably needs "
            "owner intervention, WATCH for a meaningful developing issue, NOTE for a "
            "useful "
            "non-urgent observation, and NORMAL otherwise. Candidate memories must be "
            "general property patterns supported by repeated evidence, never dossiers "
            "on "
            "individual guests. Keep the summary concise and practical.\n\n"
            f"Summary reason: {reason}\n"
            "Telemetry JSON:\n"
            f"{json.dumps(context, separators=(',', ':'), default=str)}"
        )

    @staticmethod
    def _fallback_summary(context: dict[str, Any]) -> dict[str, Any]:
        """Create a deterministic summary if no AI Task provider is available."""
        event_count = int(context["event_count"])
        anomaly_count = int(context["anomaly_count"])
        categories = (
            ", ".join(
                f"{key}: {value}"
                for key, value in sorted(context["category_counts"].items())
            )
            or "none"
        )
        return {
            "summary": (
                f"Recorded {event_count} meaningful state changes during the last "
                f"{context['period_hours']} hours. Categories: {categories}. "
                f"Baseline deviation candidates: {anomaly_count}."
            ),
            "severity": "watch" if anomaly_count else "normal",
            "confidence": 0.5,
            "observations": "AI Task is unavailable; this is a deterministic summary.",
            "anomalies": (
                f"{anomaly_count} numeric baseline deviation candidates."
                if anomaly_count
                else ""
            ),
            "maintenance_notes": "",
            "candidate_memories": "",
            "notify_owner": False,
        }

    async def _async_maybe_notify(self, summary: ObserverSummary) -> None:
        """Send configured notifications under explicit, deterministic rules."""
        notify_service = str(self.options.get(CONF_NOTIFY_SERVICE, "")).strip()
        if not notify_service or "." not in notify_service:
            return
        if summary.reason == "daily":
            should_notify = bool(self.options[CONF_NOTIFY_DAILY])
        else:
            should_notify = (
                not self.learning_only
                and summary.notify_owner
                and summary.severity in {"watch", "action"}
            )
        if not should_notify:
            return
        domain, service = notify_service.split(".", 1)
        if not self.hass.services.has_service(domain, service):
            _LOGGER.warning("Notification service %s does not exist", notify_service)
            return
        await self.hass.services.async_call(
            domain,
            service,
            {
                "title": f"{self.property_name}: {summary.severity.upper()}",
                "message": summary.summary,
            },
            blocking=False,
        )

    def _prune(self) -> None:
        """Bound retained memory by age and count."""
        cutoff = dt_util.now() - timedelta(days=int(self.options[CONF_RETENTION_DAYS]))
        self.events = [
            event
            for event in self.events
            if (timestamp := dt_util.parse_datetime(event.timestamp)) is not None
            and timestamp >= cutoff
        ][-DEFAULT_MAX_EVENTS:]
        self.summaries = self.summaries[-DEFAULT_MAX_SUMMARIES:]

    @callback
    def _schedule_save(self) -> None:
        """Coalesce storage writes."""
        self._store.async_delay_save(self._storage_payload, 15)

    @callback
    def _storage_payload(self) -> dict[str, Any]:
        """Return serializable local memory."""
        return {
            "created_at": self.created_at,
            "events": [event.as_dict() for event in self.events],
            "summaries": [summary.as_dict() for summary in self.summaries],
            "stay_context": self.stay_context,
            "patterns": self.patterns.as_dict(),
            "discovery": self.discovery,
            "discovery_activity": self.discovery_activity,
            "last_anomaly_alert": (
                self.last_anomaly_alert.isoformat() if self.last_anomaly_alert else None
            ),
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return non-sensitive operational diagnostics."""
        return {
            "property_name": self.property_name,
            "learning_only": self.learning_only,
            "tracked_entity_count": len(self.tracked_entities),
            "stored_event_count": len(self.events),
            "stored_summary_count": len(self.summaries),
            "learned_entity_count": self.patterns.learned_entity_count,
            "last_summary": self.summaries[-1].as_dict() if self.summaries else None,
            "has_stay_context": bool(self.stay_context),
            "automatic_discovery": bool(self.options[CONF_AUTO_DISCOVERY]),
            "discovery_candidate_count": len(self._discovery_candidates),
            "discovery_recommendation_count": len(
                self.discovery.get("recommendations", [])
            ),
            "last_discovery": self.discovery.get("last_run"),
        }
