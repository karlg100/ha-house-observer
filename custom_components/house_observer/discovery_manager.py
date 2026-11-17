"""Registry indexing and scheduled AI entity discovery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import Event, State, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .ai import async_generate_data, describe_error
from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_AUTO_DISCOVERY,
    CONF_DISCOVERY_INTERVAL_HOURS,
    CONF_RESERVATION_ENTITIES,
    DOMAIN,
    EVENT_DISCOVERY_COMPLETED,
)
from .discovery import (
    DiscoveryCandidate,
    eligible_candidates,
    fallback_recommendations,
    next_discovery_due,
    parse_recommendations,
    relevance_score,
)
from .prompts import DISCOVERY_STRUCTURE, build_discovery_prompt
from .repairs import async_clear_failure, async_report_failure
from .reservations import RESERVATION_CHANGED, is_reservation_state
from .selection import RegistryIndex, labels_to_flags
from .semantics import semantic_state

if TYPE_CHECKING:
    from .observer import HouseObserver

_LOGGER = logging.getLogger(__name__)

REGISTRY_EVENTS = (
    "entity_registry_updated",
    "device_registry_updated",
    "area_registry_updated",
    "label_registry_updated",
)
INDEX_REFRESH_DELAY = 10
REDISCOVERY_DELAY = 3600


class DiscoveryManager:
    """Own the registry index, discovery state, and discovery schedule."""

    def __init__(self, observer: HouseObserver) -> None:
        """Initialize the manager."""
        self.observer = observer
        self.hass = observer.hass
        self.state: dict[str, Any] = {}
        self.activity: dict[str, int] = {}
        self.candidates: dict[str, DiscoveryCandidate] = {}
        self.all_candidates: dict[str, DiscoveryCandidate] = {}
        self.index = RegistryIndex()
        self._lock = asyncio.Lock()
        self._unsubscribers: list[Callable[[], None]] = []
        self._candidate_unsubscriber: Callable[[], None] | None = None
        self._pending_index: Callable[[], None] | None = None
        self._pending_discovery: Callable[[], None] | None = None

    # ------------------------------------------------------------------ state

    def load(self, discovery: dict[str, Any], activity: dict[str, Any]) -> None:
        """Restore persisted discovery state."""
        self.state = dict(discovery)
        self.activity = {str(key): int(value) for key, value in activity.items()}

    @property
    def recommendations(self) -> list[dict[str, Any]]:
        """Return the current discovery recommendations."""
        return list(self.state.get("recommendations", []))

    @property
    def last_error(self) -> str | None:
        """Return the most recent unresolved discovery error."""
        return self.state.get("last_error")

    @property
    def failures(self) -> int:
        """Return consecutive failed discovery attempts."""
        return int(self.state.get("consecutive_failures", 0))

    @property
    def _auto(self) -> bool:
        return bool(self.observer.options[CONF_AUTO_DISCOVERY])

    # -------------------------------------------------------------- lifecycle

    @callback
    def async_start(self) -> None:
        """Refresh the index and begin scheduling. Call once HA has started."""
        self.refresh_index()
        self._bind_candidate_listener()
        for event_type in REGISTRY_EVENTS:
            self._unsubscribers.append(
                self.hass.bus.async_listen(event_type, self._async_registry_changed)
            )
        if self._auto:
            self._unsubscribers.append(
                async_track_time_interval(
                    self.hass, self._async_discovery_check, timedelta(hours=1)
                )
            )
            if self.is_due():
                self._unsubscribers.append(
                    async_call_later(self.hass, 15, self._async_discovery_check)
                )

    @callback
    def async_stop(self) -> None:
        """Cancel every listener and timer."""
        for attribute in (
            "_candidate_unsubscriber",
            "_pending_index",
            "_pending_discovery",
        ):
            if unsubscribe := getattr(self, attribute):
                unsubscribe()
                setattr(self, attribute, None)
        while self._unsubscribers:
            self._unsubscribers.pop()()

    # ---------------------------------------------------------------- indexing

    def refresh_index(self) -> None:
        """Build the privacy-conscious inventory and label index."""
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)
        area_registry = ar.async_get(self.hass)
        reservations = set(self.observer.options.get(CONF_RESERVATION_ENTITIES, []))
        index = RegistryIndex()
        all_candidates: dict[str, DiscoveryCandidate] = {}
        live: list[DiscoveryCandidate] = []

        for entry in entity_registry.entities.values():
            if entry.disabled_by is not None or entry.platform == DOMAIN:
                continue
            device = (
                device_registry.async_get(entry.device_id) if entry.device_id else None
            )
            if device is not None and device.disabled_by is not None:
                continue
            index.entity_devices[entry.entity_id] = entry.device_id

            entity_monitor, entity_ignore, entity_category = labels_to_flags(
                entry.labels
            )
            device_monitor, device_ignore, device_category = labels_to_flags(
                device.labels if device else ()
            )
            if entity_ignore or device_ignore:
                index.label_ignore.add(entry.entity_id)
            if category := entity_category or device_category:
                index.label_categories[entry.entity_id] = category

            state = self.hass.states.get(entry.entity_id)
            candidate = self._candidate(
                entry, device, area_registry, state, reservations
            )
            all_candidates[entry.entity_id] = candidate
            eligible = relevance_score(candidate) > 0
            if entity_monitor or (device_monitor and eligible):
                index.label_monitor.add(entry.entity_id)
            if entry.device_id and eligible:
                index.device_entities.setdefault(entry.device_id, []).append(
                    entry.entity_id
                )
            if state is not None and entry.hidden_by is None:
                live.append(candidate)

        self.index = index
        self.all_candidates = all_candidates
        self.candidates = {item.entity_id: item for item in eligible_candidates(live)}

    def _candidate(
        self,
        entry: er.RegistryEntry,
        device: dr.DeviceEntry | None,
        area_registry: ar.AreaRegistry,
        state: State | None,
        reservations: set[str],
    ) -> DiscoveryCandidate:
        area_id = entry.area_id or (device.area_id if device else None)
        area = area_registry.async_get_area(area_id) if area_id else None
        category = getattr(entry.entity_category, "value", entry.entity_category)
        attributes = state.attributes if state is not None else {}
        device_class = (
            attributes.get("device_class")
            or entry.device_class
            or entry.original_device_class
        )
        if state is None:
            observed = "unknown"
        elif is_reservation_state(entry.entity_id, attributes, reservations):
            observed = RESERVATION_CHANGED
        else:
            observed = semantic_state(entry.entity_id, state.state, attributes)
        return DiscoveryCandidate(
            entity_id=entry.entity_id,
            name=state.name
            if state is not None
            else str(entry.name or entry.original_name or entry.entity_id),
            state=observed,
            domain=entry.entity_id.split(".", 1)[0],
            device_class=str(device_class) if device_class else None,
            unit=attributes.get(ATTR_UNIT_OF_MEASUREMENT),
            entity_category=str(category) if category else None,
            device_id=entry.device_id,
            device_name=str(device.name_by_user or device.name) if device else None,
            area_id=area_id,
            area_name=str(area.name) if area else "Unassigned",
            activity_count=int(self.activity.get(entry.entity_id, 0)),
        )

    @callback
    def _bind_candidate_listener(self) -> None:
        """Count state changes of eligible candidates, without retaining states."""
        if self._candidate_unsubscriber:
            self._candidate_unsubscriber()
            self._candidate_unsubscriber = None
        if self._auto and self.candidates:
            self._candidate_unsubscriber = async_track_state_change_event(
                self.hass, list(self.candidates), self._async_candidate_changed
            )

    @callback
    def _async_candidate_changed(self, event: Event) -> None:
        entity_id = str(event.data.get("entity_id", ""))
        old_state: State | None = event.data.get("old_state")
        new_state: State | None = event.data.get("new_state")
        if not entity_id or new_state is None:
            return
        if old_state is not None and old_state.state == new_state.state:
            return
        count = min(1_000_000, int(self.activity.get(entity_id, 0)) + 1)
        self.activity[entity_id] = count
        if candidate := self.candidates.get(entity_id):
            candidate.activity_count = count
        self.observer.schedule_save()

    @callback
    def _async_registry_changed(self, _event: Event) -> None:
        """Apply label and registry changes quickly; rediscover later."""
        if self._pending_index:
            self._pending_index()
        self._pending_index = async_call_later(
            self.hass, INDEX_REFRESH_DELAY, self._async_reindex
        )
        if self._auto:
            if self._pending_discovery:
                self._pending_discovery()
            self._pending_discovery = async_call_later(
                self.hass, REDISCOVERY_DELAY, self._async_registry_discovery
            )

    @callback
    def _async_reindex(self, _now: datetime) -> None:
        self._pending_index = None
        self.refresh_index()
        self._bind_candidate_listener()
        self.observer.async_selection_changed()

    @callback
    def _async_registry_discovery(self, _now: datetime) -> None:
        self._pending_discovery = None
        if self._auto:
            self._create_task("registry_change")

    # -------------------------------------------------------------- schedule

    def successful_runs(self) -> int:
        """Return the persisted successful AI run count."""
        default = 1 if self.state.get("ai_generated") else 0
        try:
            return max(0, int(self.state.get("successful_ai_runs", default)))
        except (TypeError, ValueError):
            return default

    def next_run(self) -> datetime | None:
        """Return the next scheduled automatic discovery time."""
        last_run = dt_util.parse_datetime(str(self.state.get("last_run", "")))
        if last_run is None:
            return None
        started = dt_util.parse_datetime(
            str(self.state.get("bootstrap_started_at", ""))
        )
        return next_discovery_due(
            last_run=last_run,
            bootstrap_started_at=started or last_run,
            successful_ai_runs=self.successful_runs(),
            ai_generated=bool(self.state.get("ai_generated")),
            configured_interval_hours=int(
                self.observer.options[CONF_DISCOVERY_INTERVAL_HOURS]
            ),
        )

    def is_due(self, now: datetime | None = None) -> bool:
        """Return whether automatic discovery should run."""
        next_run = self.next_run()
        return next_run is None or (now or dt_util.now()) >= next_run

    @callback
    def _async_discovery_check(self, now: datetime) -> None:
        if self._auto and not self._lock.locked() and self.is_due(now):
            self._create_task("scheduled")

    @callback
    def _create_task(self, reason: str) -> None:
        self.observer.entry.async_create_task(
            self.hass,
            self.async_discover(reason=reason),
            f"Discover important {self.observer.property_name} entities",
        )

    # -------------------------------------------------------------- discovery

    async def async_discover(self, *, reason: str) -> dict[str, Any]:
        """Run one serialized discovery and always record the attempt."""
        async with self._lock:
            return await self._async_discover_locked(reason)

    async def _async_discover_locked(self, reason: str) -> dict[str, Any]:
        observer = self.observer
        self.refresh_index()
        candidates = list(self.candidates.values())
        recommendations: list[dict[str, Any]] = []
        error: str | None = None

        if candidates:
            by_area: dict[str, list[dict[str, Any]]] = {}
            for candidate in candidates:
                by_area.setdefault(candidate.area_name, []).append(
                    candidate.prompt_dict()
                )
            try:
                data = await async_generate_data(
                    self.hass,
                    task_name=f"{observer.property_name} entity discovery",
                    instructions=build_discovery_prompt(
                        mode=observer.mode, inventory_by_area=by_area
                    ),
                    structure=DISCOVERY_STRUCTURE,
                    entity_id=observer.options.get(CONF_AI_TASK_ENTITY),
                )
                recommendations = parse_recommendations(
                    data.get("recommendations"),
                    candidates,
                    confidence=data.get("confidence", 0),
                )
                if not recommendations:
                    error = "AI response contained no usable entity recommendations"
                    _LOGGER.warning(
                        "House Observer discovery for %s returned no usable rows: %r",
                        observer.property_name,
                        str(data.get("recommendations"))[:500],
                    )
            except Exception as err:  # noqa: BLE001 - any failure must be recorded
                error = describe_error(err)
                _LOGGER.warning(
                    "House Observer discovery for %s failed: %s",
                    observer.property_name,
                    error,
                    exc_info=_LOGGER.isEnabledFor(logging.DEBUG),
                )
        else:
            error = "No eligible entities were found to review"

        ai_generated = bool(recommendations)
        if not recommendations:
            recommendations = fallback_recommendations(candidates)

        completed_at = dt_util.now()
        successful_runs = self.successful_runs()
        bootstrap = self.state.get("bootstrap_started_at")
        if not bootstrap and successful_runs:
            bootstrap = self.state.get("last_run")
        if ai_generated:
            successful_runs += 1
            bootstrap = bootstrap or completed_at.isoformat()

        failures = 0 if ai_generated else self.failures + 1
        self.state = {
            "last_run": completed_at.isoformat(),
            "reason": reason,
            "ai_generated": ai_generated,
            "candidate_count": len(candidates),
            "successful_ai_runs": successful_runs,
            "bootstrap_started_at": bootstrap,
            "recommendations": recommendations,
            "last_error": error,
            "last_error_at": completed_at.isoformat() if error else None,
            "consecutive_failures": failures,
        }
        self.activity = dict.fromkeys(self.activity, 0)

        if ai_generated:
            async_clear_failure(
                self.hass, kind="discovery_failed", entry_id=observer.entry.entry_id
            )
        else:
            async_report_failure(
                self.hass,
                kind="discovery_failed",
                entry_id=observer.entry.entry_id,
                property_name=observer.property_name,
                failures=failures,
                error=error or "unknown error",
            )

        observer.async_selection_changed()
        observer.schedule_save()
        response = self.response()
        self.hass.bus.async_fire(EVENT_DISCOVERY_COMPLETED, response)
        return response

    def response(self) -> dict[str, Any]:
        """Return a bounded, user-facing discovery result."""
        recommendations = self.recommendations
        device_ids = {item.get("device_id") for item in recommendations} - {None}
        successful_runs = self.successful_runs()
        next_run = self.next_run()
        if not self.state.get("last_run"):
            phase = "initial"
        elif not self.state.get("ai_generated"):
            phase = "daily_retry"
        elif successful_runs <= 3:
            phase = ("24_hour_followup", "3_day_followup", "7_day_followup")[
                successful_runs - 1
            ]
        else:
            phase = "mature"
        return {
            "last_run": self.state.get("last_run"),
            "next_run": next_run.isoformat() if next_run else None,
            "reason": self.state.get("reason"),
            "ai_generated": bool(self.state.get("ai_generated")),
            "successful_ai_runs": successful_runs,
            "schedule_phase": phase,
            "candidate_count": int(self.state.get("candidate_count", 0)),
            "recommended_device_count": len(device_ids),
            "recommended_entity_count": len(recommendations),
            "tracked_entity_count": len(self.observer.tracked_entities),
            "last_error": self.last_error,
            "last_error_at": self.state.get("last_error_at"),
            "consecutive_failures": self.failures,
            "recommendations": recommendations[:100],
        }
