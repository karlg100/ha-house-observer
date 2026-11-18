"""Orchestrates observation, analysis, and memory for one property."""

from __future__ import annotations

import asyncio
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
from homeassistant.core import Context, Event, HomeAssistant, State, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .ai import (
    AITaskBadResponse,
    AITaskUnavailable,
    async_generate_data,
    describe_error,
)
from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_ALWAYS_MONITOR_DEVICES,
    CONF_ALWAYS_MONITOR_ENTITIES,
    CONF_ANALYSIS_AGENT,
    CONF_AUTO_DISCOVERY,
    CONF_DISCOVERY_INTERVAL_HOURS,
    CONF_LEARNING_ONLY,
    CONF_LOOKBACK_HOURS,
    CONF_MIN_SAMPLES,
    CONF_MODE,
    CONF_NEVER_MONITOR_DEVICES,
    CONF_NEVER_MONITOR_ENTITIES,
    CONF_NOTIFY_DAILY,
    CONF_NOTIFY_SERVICE,
    CONF_OBSERVER_GUIDANCE,
    CONF_OCCUPANCY_ENTITY,
    CONF_PROPERTY_NAME,
    CONF_RESERVATION_ENTITIES,
    CONF_RETENTION_DAYS,
    CONF_SUMMARY_TIME,
    CONF_ZSCORE_THRESHOLD,
    DATA_CATEGORY_OVERRIDES,
    DEFAULT_ANOMALY_COOLDOWN_MINUTES,
    DEFAULT_AUTO_DISCOVERY,
    DEFAULT_BASELINE_HALF_LIFE_DAYS,
    DEFAULT_DISCOVERY_INTERVAL_HOURS,
    DEFAULT_EVENT_WINDOW_SECONDS,
    DEFAULT_LEARNING_ONLY,
    DEFAULT_LOOKBACK_HOURS,
    DEFAULT_MAX_EVENTS,
    DEFAULT_MAX_SUMMARIES,
    DEFAULT_MIN_SAMPLES,
    DEFAULT_MODE,
    DEFAULT_NOTIFY_DAILY,
    DEFAULT_OBSERVER_GUIDANCE,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_SUMMARY_TIME,
    DEFAULT_ZSCORE_THRESHOLD,
    EVENT_SUMMARY_GENERATED,
    MODE_RENTAL,
    MODES,
    SEVERITIES,
)
from .discovery import default_category
from .discovery_manager import DiscoveryManager
from .guidance import normalize_guidance
from .houselog import DEFAULT_BUDGET_CHARS, HouseLog, render_house_log
from .ingest import NumericThrottle, minutes_since, safe_attributes
from .knowledge import (
    knowledge_context,
    new_note,
    previous_summaries,
    prune_knowledge,
    recurring_candidate_memories,
)
from .models import KnowledgeItem, ObservationEvent, ObserverSummary
from .notify import async_send, should_notify
from .occupancy import (
    next_arrival,
    resolve_occupancy,
    stay_expired,
    stay_reservation,
)
from .patterns import PatternEngine, numeric_value
from .prompts import SUMMARY_STRUCTURE, build_summary_prompt, parse_json_reply
from .repairs import async_clear_failure, async_report_failure
from .reservations import (
    RESERVATION_CHANGED,
    is_reservation_state,
    normalize_reservations,
    safe_reservation_attributes,
)
from .selection import SelectionInputs, category_for, effective_entities
from .semantics import semantic_state
from .store import ObserverStore
from .telemetry import (
    async_recorder_points,
    build_series,
    recorder_available,
    stored_event_points,
)

_LOGGER = logging.getLogger(__name__)

MAX_PROMPT_EVENTS = 250
MAX_PROMPT_ANOMALIES = 50
MAX_PROMPT_STATES = 150
MAX_PROMPT_BASELINES = 50
OPEN_STATES = {"open", "opening", "unlocked", "unlocking", "jammed"}


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


def _output_limited(error: str) -> bool:
    """Return whether a provider error means the reply ran out of tokens."""
    text = error.lower()
    return any(
        marker in text
        for marker in (
            "token limit",
            "max_tokens",
            "max tokens",
            "output limit",
            "length limit",
        )
    )


def _limited_text(value: Any, limit: int = 4000) -> str:
    """Bound model and owner text stored in entity attributes."""
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


class HouseObserver:
    """Observe selected Home Assistant entities for one property."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize an observer."""
        self.hass = hass
        self.entry = entry
        self.events: list[ObservationEvent] = []
        self.summaries: list[ObserverSummary] = []
        self.knowledge: list[KnowledgeItem] = []
        self.stay_context: dict[str, Any] = {}
        self.patterns = PatternEngine()
        self.created_at = dt_util.now().isoformat()
        self.last_anomaly_alert: datetime | None = None
        self.summary_failures = 0
        self.last_summary_error: str | None = None
        self.last_summary_error_at: str | None = None
        self.last_agent_error: str | None = None
        self.last_house_log: dict[str, Any] | None = None
        self.discovery = DiscoveryManager(self)
        self._tracked: list[str] = []
        self._throttle = NumericThrottle(DEFAULT_EVENT_WINDOW_SECONDS)
        self._store = ObserverStore(
            hass,
            entry.entry_id,
            set(entry.options.get(CONF_RESERVATION_ENTITIES, [])),
        )
        self._unsubscribers: list[Callable[[], None]] = []
        self._observation_unsubscriber: Callable[[], None] | None = None
        self._listeners: set[Callable[[], None]] = set()
        self._lock = asyncio.Lock()

    # ----------------------------------------------------------- configuration

    @property
    def property_name(self) -> str:
        """Return the configured property name."""
        return str(self.entry.data[CONF_PROPERTY_NAME])

    @property
    def options(self) -> dict[str, Any]:
        """Return options with defaults filled in."""
        return {
            CONF_MODE: DEFAULT_MODE,
            CONF_LOOKBACK_HOURS: DEFAULT_LOOKBACK_HOURS,
            CONF_RETENTION_DAYS: DEFAULT_RETENTION_DAYS,
            CONF_LEARNING_ONLY: DEFAULT_LEARNING_ONLY,
            CONF_NOTIFY_DAILY: DEFAULT_NOTIFY_DAILY,
            CONF_MIN_SAMPLES: DEFAULT_MIN_SAMPLES,
            CONF_ZSCORE_THRESHOLD: DEFAULT_ZSCORE_THRESHOLD,
            CONF_SUMMARY_TIME: DEFAULT_SUMMARY_TIME,
            CONF_AUTO_DISCOVERY: DEFAULT_AUTO_DISCOVERY,
            CONF_DISCOVERY_INTERVAL_HOURS: DEFAULT_DISCOVERY_INTERVAL_HOURS,
            CONF_OBSERVER_GUIDANCE: DEFAULT_OBSERVER_GUIDANCE,
            **self.entry.options,
        }

    @property
    def mode(self) -> str:
        """Return home or rental."""
        mode = str(self.options[CONF_MODE])
        return mode if mode in MODES else DEFAULT_MODE

    @property
    def learning_only(self) -> bool:
        """Return whether proactive notifications are suppressed."""
        return bool(self.options[CONF_LEARNING_ONLY])

    @property
    def observer_guidance(self) -> str:
        """Return bounded persistent owner guidance for AI summaries."""
        return normalize_guidance(self.options.get(CONF_OBSERVER_GUIDANCE, ""))

    @property
    def tracked_entities(self) -> list[str]:
        """Return the effective monitored entity set."""
        return list(self._tracked)

    @property
    def status(self) -> str:
        """Return learning, active, or error."""
        discovery_failing = (
            bool(self.options[CONF_AUTO_DISCOVERY]) and self.discovery.failures > 0
        )
        if discovery_failing or self.summary_failures > 0:
            return "error"
        return "learning" if self.learning_only else "active"

    @property
    def last_error(self) -> dict[str, Any] | None:
        """Return the newest unresolved error, if any."""
        errors = []
        if self.summary_failures and self.last_summary_error:
            errors.append(
                ("summary", self.last_summary_error, self.last_summary_error_at)
            )
        if self.discovery.failures and self.discovery.last_error:
            errors.append(
                (
                    "discovery",
                    self.discovery.last_error,
                    self.discovery.state.get("last_error_at"),
                )
            )
        if not errors:
            return None
        source, message, when = max(errors, key=lambda item: item[2] or "")
        return {"source": source, "message": message, "at": when}

    # --------------------------------------------------------------- lifecycle

    async def async_start(self) -> None:
        """Load memory and begin observing."""
        stored = await self._store.async_load() or {}
        self.events = [
            ObservationEvent.from_dict(value) for value in stored.get("events", [])
        ]
        self.summaries = [
            ObserverSummary.from_dict(value) for value in stored.get("summaries", [])
        ]
        self.knowledge = [
            KnowledgeItem.from_dict(value) for value in stored.get("knowledge", [])
        ]
        self.stay_context = dict(stored.get("stay_context", {}))
        self.discovery.load(
            stored.get("discovery", {}), stored.get("discovery_activity", {})
        )
        self.created_at = str(stored.get("created_at", self.created_at))
        self.summary_failures = int(stored.get("summary_failures", 0))
        self.last_summary_error = stored.get("last_summary_error")
        self.last_summary_error_at = stored.get("last_summary_error_at")
        self.patterns = PatternEngine(
            stored.get("patterns"),
            minimum_samples=int(self.options[CONF_MIN_SAMPLES]),
            zscore_threshold=float(self.options[CONF_ZSCORE_THRESHOLD]),
            half_life_days=DEFAULT_BASELINE_HALF_LIFE_DAYS,
        )
        if value := stored.get("last_anomaly_alert"):
            self.last_anomaly_alert = dt_util.parse_datetime(str(value))

        self._prune()
        self.async_selection_changed()

        hour, minute, second = self._summary_time_parts()
        self._unsubscribers.extend(
            [
                async_at_started(self.hass, self._async_hass_started),
                async_track_time_change(
                    self.hass,
                    self._async_daily_summary,
                    hour=hour,
                    minute=minute,
                    second=second,
                ),
                async_track_time_interval(
                    self.hass, self._async_hourly_maintenance, timedelta(hours=1)
                ),
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STOP, self._async_home_assistant_stop
                ),
            ]
        )

    @callback
    def _async_hass_started(self, _hass: HomeAssistant) -> None:
        """Index registries once every integration has created its entities."""
        self.discovery.async_start()
        self.async_selection_changed()

    async def async_stop(self) -> None:
        """Stop observing and persist immediately."""
        if self._observation_unsubscriber:
            self._observation_unsubscriber()
            self._observation_unsubscriber = None
        self.discovery.async_stop()
        while self._unsubscribers:
            self._unsubscribers.pop()()
        await self._store.async_save(self._storage_payload())

    async def _async_home_assistant_stop(self, _event: Event) -> None:
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
    def notify_listeners(self) -> None:
        """Notify observer entities of new data."""
        for listener in list(self._listeners):
            listener()

    @callback
    def schedule_save(self) -> None:
        """Coalesce storage writes."""
        self._store.async_delay_save(self._storage_payload, 15)

    # --------------------------------------------------------------- selection

    @callback
    def async_selection_changed(self) -> None:
        """Recompute the monitored set and listen only to it."""
        options = self.options
        tracked = effective_entities(
            self.discovery.index,
            SelectionInputs(
                auto_discovery=bool(options[CONF_AUTO_DISCOVERY]),
                recommendations=self.discovery.recommendations,
                pinned_entities=options.get(CONF_ALWAYS_MONITOR_ENTITIES, []),
                pinned_devices=options.get(CONF_ALWAYS_MONITOR_DEVICES, []),
                ignored_entities=options.get(CONF_NEVER_MONITOR_ENTITIES, []),
                ignored_devices=options.get(CONF_NEVER_MONITOR_DEVICES, []),
            ),
        )
        if tracked != self._tracked or self._observation_unsubscriber is None:
            self._throttle.forget(set(self._tracked) - set(tracked))
            self._tracked = tracked
            if self._observation_unsubscriber:
                self._observation_unsubscriber()
                self._observation_unsubscriber = None
            if tracked:
                self._observation_unsubscriber = async_track_state_change_event(
                    self.hass, tracked, self._async_state_changed
                )
        self.notify_listeners()

    def category_for(self, entity_id: str) -> str:
        """Return an entity's operational category."""
        candidate = self.discovery.all_candidates.get(entity_id)
        return category_for(
            entity_id,
            index=self.discovery.index,
            overrides=dict(self.entry.data.get(DATA_CATEGORY_OVERRIDES, {})),
            recommendations=self.discovery.recommendations,
            fallback=default_category(candidate) if candidate else "other",
        )

    def _reservation_entities(self) -> set[str]:
        return set(self.options.get(CONF_RESERVATION_ENTITIES, []))

    # ----------------------------------------------------------------- ingest

    @callback
    def _async_state_changed(self, event: Event) -> None:
        self.entry.async_create_task(
            self.hass,
            self._async_process_state_change(event),
            f"Process {self.property_name} observation",
            eager_start=True,
        )

    async def _async_process_state_change(self, event: Event) -> None:
        """Convert a raw state change into a bounded observation."""
        old_state: State | None = event.data.get("old_state")
        new_state: State | None = event.data.get("new_state")
        if new_state is None or new_state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return
        if old_state is not None and old_state.state == new_state.state:
            return

        entity_id = new_state.entity_id
        observed_at = dt_util.as_local(new_state.last_updated)
        reservation = is_reservation_state(
            entity_id, new_state.attributes, self._reservation_entities()
        )
        if reservation:
            category = "reservation"
            observed_state = RESERVATION_CHANGED
            observed_old = RESERVATION_CHANGED if old_state is not None else None
            attributes = safe_reservation_attributes(new_state.attributes)
        else:
            category = self.category_for(entity_id)
            observed_state = semantic_state(
                entity_id, new_state.state, new_state.attributes
            )
            observed_old = (
                semantic_state(entity_id, old_state.state, old_state.attributes)
                if old_state is not None
                else None
            )
            attributes = safe_attributes(dict(new_state.attributes))

        async with self._lock:
            anomaly = self.patterns.observe(entity_id, observed_state, observed_at)
            numeric = numeric_value(observed_state)
            if numeric is not None:
                window = self._throttle.offer(
                    entity_id, observed_at, numeric, anomalous=anomaly is not None
                )
                if window is None:
                    self.schedule_save()
                    return
                attributes.update(window)

            self.events.append(
                ObservationEvent(
                    timestamp=observed_at.isoformat(),
                    entity_id=entity_id,
                    name=str(
                        new_state.attributes.get(ATTR_FRIENDLY_NAME, new_state.name)
                    ),
                    category=category,
                    old_state=observed_old,
                    new_state=observed_state,
                    unit=new_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT),
                    attributes=attributes,
                    anomaly=anomaly,
                )
            )
            if len(self.events) > DEFAULT_MAX_EVENTS:
                self._prune()
            self.schedule_save()
            self.notify_listeners()

        if anomaly and not self.learning_only and self._anomaly_alert_due(observed_at):
            self.last_anomaly_alert = observed_at
            self.entry.async_create_task(
                self.hass,
                self.async_generate_summary(reason="anomaly", hours=2),
                f"Generate {self.property_name} anomaly summary",
            )

    def _anomaly_alert_due(self, now: datetime) -> bool:
        if self.last_anomaly_alert is None:
            return True
        return now - self.last_anomaly_alert >= timedelta(
            minutes=DEFAULT_ANOMALY_COOLDOWN_MINUTES
        )

    # ------------------------------------------------------------ maintenance

    @callback
    def _async_hourly_maintenance(self, now: datetime) -> None:
        """Expire a finished stay and prune bounded memory."""
        changed = False
        if self.stay_context and stay_expired(self.stay_context, dt_util.now()):
            self.stay_context = {}
            changed = True
        before = len(self.knowledge)
        self._prune()
        if changed or before != len(self.knowledge):
            self.schedule_save()
            self.notify_listeners()

    def _prune(self) -> None:
        """Bound retained memory by age and count."""
        now = dt_util.now()
        cutoff = now - timedelta(days=int(self.options[CONF_RETENTION_DAYS]))
        self.events = [
            event
            for event in self.events
            if (when := event.when) is not None and when >= cutoff
        ][-DEFAULT_MAX_EVENTS:]
        self.summaries = self.summaries[-DEFAULT_MAX_SUMMARIES:]
        self.knowledge = prune_knowledge(self.knowledge, now)

    def events_since(self, hours: float) -> list[ObservationEvent]:
        """Return recent events."""
        cutoff = dt_util.now() - timedelta(hours=hours)
        return [
            event
            for event in self.events
            if (when := event.when) is not None and when >= cutoff
        ]

    # -------------------------------------------------------------- knowledge

    async def async_record_note(
        self,
        note: str,
        category: str = "owner",
        expires_in_days: float | None = None,
    ) -> KnowledgeItem:
        """Add durable property knowledge that every summary will see."""
        item = new_note(note, category, dt_util.now(), expires_in_days=expires_in_days)
        self.knowledge.append(item)
        self._prune()
        self.schedule_save()
        self.notify_listeners()
        return item

    async def async_forget_note(self, note_id: str) -> bool:
        """Remove a knowledge item, returning whether it existed."""
        before = len(self.knowledge)
        self.knowledge = [item for item in self.knowledge if item.note_id != note_id]
        if removed := len(self.knowledge) != before:
            self.schedule_save()
            self.notify_listeners()
        return removed

    # -------------------------------------------------------------- occupancy

    async def async_set_stay_context(self, context: dict[str, Any]) -> None:
        """Set or clear the manually supplied stay."""
        self.stay_context = context
        self.schedule_save()
        self.notify_listeners()

    def occupancy_context(
        self, now: datetime | None = None
    ) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
        """Return (occupancy, normalized stay, normalized reservations)."""
        now = now or dt_util.now()
        occupancy_entity = self.options.get(CONF_OCCUPANCY_ENTITY)
        entity_state = (
            state.state
            if occupancy_entity and (state := self.hass.states.get(occupancy_entity))
            else None
        )
        if self.mode != MODE_RENTAL:
            # Stays and booking calendars only mean occupancy for a rental.
            occupancy = resolve_occupancy(
                entity_state=entity_state, stay=None, reservations=[]
            )
            occupancy["next_arrival"] = None
            return occupancy, None, []

        stay = (
            stay_reservation(self.stay_context, now)
            if self.stay_context and not stay_expired(self.stay_context, now)
            else None
        )
        reservation_values: list[dict[str, Any]] = []
        for entity_id in sorted(self._reservation_entities() | set(self._tracked)):
            state = self.hass.states.get(entity_id)
            if state is not None and is_reservation_state(
                entity_id, state.attributes, self._reservation_entities()
            ):
                reservation_values.append(
                    {"entity_id": entity_id, "attributes": state.attributes}
                )
        reservations = normalize_reservations(reservation_values, now)
        occupancy = resolve_occupancy(
            entity_state=entity_state, stay=stay, reservations=reservations
        )
        occupancy["next_arrival"] = next_arrival(stay, reservations)
        return occupancy, stay, reservations

    # ---------------------------------------------------------------- summary

    @callback
    def _async_daily_summary(self, _now: datetime) -> None:
        self.entry.async_create_task(
            self.hass,
            self.async_generate_summary(
                reason="daily", hours=int(self.options[CONF_LOOKBACK_HOURS])
            ),
            f"Generate {self.property_name} daily summary",
        )

    def _summary_time_parts(self) -> tuple[int, int, int]:
        raw = self.options.get(CONF_SUMMARY_TIME, DEFAULT_SUMMARY_TIME)
        if hasattr(raw, "hour"):
            return int(raw.hour), int(raw.minute), int(raw.second)
        try:
            parts = [int(value) for value in str(raw).split(":")]
            return parts[0], parts[1], parts[2] if len(parts) > 2 else 0
        except (TypeError, ValueError, IndexError):
            return 8, 0, 0

    async def async_generate_summary(
        self, *, reason: str = "manual", hours: int | None = None
    ) -> ObserverSummary:
        """Generate and store an operational summary."""
        period_hours = hours or int(self.options[CONF_LOOKBACK_HOURS])
        context, house_log = await self.async_build_context(period_hours)
        occupancy = context["occupancy"]["state"]
        result: dict[str, Any] | None = None
        error: str | None = None
        path = "fallback"
        prompt_args = {
            "mode": self.mode,
            "occupancy": occupancy,
            "guidance": self.observer_guidance,
            "reason": reason,
            "context": context,
            "house_log": house_log.text if house_log else None,
        }

        if agent_id := self.options.get(CONF_ANALYSIS_AGENT):
            try:
                result = await self._async_agent_summary(
                    agent_id, build_summary_prompt(**prompt_args, agent=True)
                )
                path = "agent"
                self.last_agent_error = None
            except Exception as err:  # noqa: BLE001 - fall back to AI Task
                self.last_agent_error = describe_error(err)
                _LOGGER.warning(
                    "House Observer analysis agent %s failed for %s; using AI "
                    "Task instead: %s",
                    agent_id,
                    self.property_name,
                    self.last_agent_error,
                )

        if result is None:
            for compact in (False, True):
                try:
                    result = await async_generate_data(
                        self.hass,
                        task_name=f"{self.property_name} house observer summary",
                        instructions=build_summary_prompt(
                            **prompt_args, compact=compact
                        ),
                        structure=SUMMARY_STRUCTURE,
                        entity_id=self.options.get(CONF_AI_TASK_ENTITY),
                    )
                    path = "ai_task_compact" if compact else "ai_task"
                    error = None
                    break
                except AITaskUnavailable as err:
                    error = str(err)
                    break
                except Exception as err:  # noqa: BLE001 - any failure must be recorded
                    error = describe_error(err)
                    if not compact and _output_limited(error):
                        _LOGGER.info(
                            "House Observer summary for %s hit the output limit; "
                            "retrying with a shorter reply",
                            self.property_name,
                        )
                        continue
                    _LOGGER.warning(
                        "House Observer summary for %s failed: %s",
                        self.property_name,
                        error,
                        exc_info=_LOGGER.isEnabledFor(logging.DEBUG),
                    )
                    break

        self._record_summary_outcome(error)
        if result is None:
            result = self._fallback_summary(context, error or "unknown error")

        severity = str(result.get("severity", "normal")).strip().lower()
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
            ai_generated=error is None,
            mode=self.mode,
            occupancy=occupancy,
            analysis_path=path,
        )
        self.summaries.append(summary)
        self.summaries = self.summaries[-DEFAULT_MAX_SUMMARIES:]
        self.schedule_save()
        self.notify_listeners()
        self.hass.bus.async_fire(EVENT_SUMMARY_GENERATED, summary.as_dict())
        if (target := str(self.options.get(CONF_NOTIFY_SERVICE) or "")) and (
            should_notify(
                summary,
                learning_only=self.learning_only,
                notify_daily=bool(self.options[CONF_NOTIFY_DAILY]),
            )
        ):
            await async_send(
                self.hass,
                target,
                title=f"{self.property_name}: {summary.severity.upper()}",
                message=summary.summary,
            )
        return summary

    async def _async_agent_summary(self, agent_id: str, prompt: str) -> dict[str, Any]:
        """Ask a tool-enabled conversation agent and parse its JSON reply."""
        from homeassistant.components import conversation  # noqa: PLC0415
        from homeassistant.helpers import intent  # noqa: PLC0415

        result = await conversation.async_converse(
            self.hass,
            text=prompt,
            conversation_id=None,
            context=Context(),
            agent_id=agent_id,
        )
        response = result.response
        speech = str((response.speech.get("plain") or {}).get("speech", ""))
        if response.response_type == intent.IntentResponseType.ERROR:
            raise AITaskBadResponse(f"Agent returned an error: {speech[:200]}")
        data = parse_json_reply(speech)
        if data is None:
            raise AITaskBadResponse("Agent reply did not contain the summary JSON")
        return data

    async def async_house_log(
        self,
        start: datetime,
        end: datetime,
        *,
        entity_ids: list[str] | None = None,
        budget_chars: int = DEFAULT_BUDGET_CHARS,
    ) -> HouseLog | None:
        """Render the compact log for a time range."""
        ids = list(entity_ids or self._tracked)
        if not ids:
            return None
        raw = None
        if recorder_available(self.hass):
            try:
                raw = await async_recorder_points(self.hass, ids, start, end)
            except Exception as err:  # noqa: BLE001 - fall back to stored events
                _LOGGER.warning(
                    "House Observer could not read recorder history: %s",
                    describe_error(err),
                )
        if raw is None:
            raw = stored_event_points(self.events, set(ids), start)
        series = build_series(
            self.hass,
            raw,
            category_for=self.category_for,
            spread_for=self._spread_for,
            reservation_ids=self._reservation_entities(),
        )
        if not series:
            return None
        log = render_house_log(series, start, end, budget_chars=budget_chars)
        self.last_house_log = {
            "built_at": dt_util.now().isoformat(),
            "hours": round((end - start).total_seconds() / 3600, 1),
            "characters": len(log.text),
            "entities": log.entity_count,
            "raw_state_changes": log.raw_points,
            "timeline_included": log.timeline_included,
            "tolerance_multiplier": log.tolerance_multiplier,
            "truncated": log.truncated,
            "source": "recorder" if recorder_available(self.hass) else "stored_events",
        }
        return log

    def _spread_for(self, entity_id: str) -> float | None:
        record = self.patterns.entities.get(entity_id)
        if record is None:
            return None
        stats = record["numeric"]
        return stats.standard_deviation if stats.weight >= 10 else None

    def _record_summary_outcome(self, error: str | None) -> None:
        entry_id = self.entry.entry_id
        if error is None:
            self.summary_failures = 0
            self.last_summary_error = None
            self.last_summary_error_at = None
            async_clear_failure(self.hass, kind="summary_failed", entry_id=entry_id)
            return
        self.summary_failures += 1
        self.last_summary_error = error
        self.last_summary_error_at = dt_util.now().isoformat()
        async_report_failure(
            self.hass,
            kind="summary_failed",
            entry_id=entry_id,
            property_name=self.property_name,
            failures=self.summary_failures,
            error=error,
        )

    async def async_build_context(
        self, hours: int
    ) -> tuple[dict[str, Any], HouseLog | None]:
        """Build the model context and the compact house log."""
        now = dt_util.now()
        house_log = await self.async_house_log(now - timedelta(hours=hours), now)
        recent = self.events_since(hours)
        category_counts: dict[str, int] = {}
        anomaly_count = 0
        for event in recent:
            category_counts[event.category] = category_counts.get(event.category, 0) + 1
            anomaly_count += event.anomaly is not None

        occupancy, stay, reservations = self.occupancy_context(now)
        reservation_ids = self._reservation_entities()
        current_states: list[dict[str, Any]] = []
        for entity_id in self._tracked:
            state = self.hass.states.get(entity_id)
            if state is None or is_reservation_state(
                entity_id, state.attributes, reservation_ids
            ):
                continue
            current_states.append(
                {
                    "entity_id": entity_id,
                    "name": state.name,
                    "category": self.category_for(entity_id),
                    "state": semantic_state(entity_id, state.state, state.attributes),
                    "for_minutes": minutes_since(state.last_changed, now),
                    "unit": state.attributes.get(ATTR_UNIT_OF_MEASUREMENT),
                    "attributes": safe_attributes(dict(state.attributes)),
                }
            )

        # With a house log the model reads every change there; raw events are
        # only sent when no log could be built.
        event_payload = (
            []
            if house_log is not None
            else [event.as_dict() for event in recent[-MAX_PROMPT_EVENTS:]]
        )
        anomalies = [
            {
                "time": event.timestamp,
                "entity": event.name,
                **(event.anomaly or {}),
            }
            for event in recent
            if event.anomaly
        ][-MAX_PROMPT_ANOMALIES:]
        context: dict[str, Any] = {
            "property": self.property_name,
            "mode": self.mode,
            "generated_at": now.isoformat(),
            "period_hours": hours,
            "learning_only": self.learning_only,
            "occupancy": occupancy,
            "property_knowledge": knowledge_context(self.knowledge),
            "previous_summaries": previous_summaries(self.summaries),
            "unconfirmed_patterns": recurring_candidate_memories(
                self.summaries,
                exclude={item.text for item in self.knowledge},
            ),
            "event_count": len(recent),
            "omitted_older_events": (
                0 if house_log is not None else max(0, len(recent) - len(event_payload))
            ),
            "baseline_deviations": anomalies,
            "category_counts": category_counts,
            "anomaly_count": anomaly_count,
            "open_or_unlocked": [
                {"name": item["name"], "for_minutes": item["for_minutes"]}
                for item in current_states
                if item["state"] in OPEN_STATES
            ],
            "current_states": current_states[:MAX_PROMPT_STATES],
            "omitted_current_states": max(0, len(current_states) - MAX_PROMPT_STATES),
            **({"recent_events": event_payload} if event_payload else {}),
            "learned_baselines": self.patterns.baseline_context(
                limit=MAX_PROMPT_BASELINES, entity_ids=set(self._tracked)
            ),
        }
        if stay is not None:
            context["stay"] = stay
        if reservations:
            context["reservations"] = reservations
        if house_log is not None:
            context["house_log_stats"] = {
                "entities": house_log.entity_count,
                "raw_state_changes": house_log.raw_points,
                "timeline_included": house_log.timeline_included,
                "truncated": house_log.truncated,
            }
        return context, house_log

    def _fallback_summary(self, context: dict[str, Any], error: str) -> dict[str, Any]:
        """Create a deterministic summary when AI analysis is unavailable."""
        anomaly_count = int(context["anomaly_count"])
        categories = (
            ", ".join(
                f"{key}: {value}"
                for key, value in sorted(context["category_counts"].items())
            )
            or "none"
        )
        left_open = [
            f"{item['name']} ({item['for_minutes']} min)"
            for item in context["open_or_unlocked"]
            if (item["for_minutes"] or 0) >= 30
        ]
        severity = "normal"
        if anomaly_count or left_open:
            severity = "watch"
        maintenance = (
            "Open or unlocked for 30+ minutes: " + ", ".join(left_open)
            if left_open
            else ""
        )
        return {
            "summary": (
                f"Recorded {context['event_count']} meaningful state changes during "
                f"the last {context['period_hours']} hours. Categories: {categories}. "
                f"Baseline deviation candidates: {anomaly_count}. "
                f"Occupancy: {context['occupancy']['state']}."
            ),
            "severity": severity,
            "confidence": 0.5,
            "observations": (
                f"AI analysis was unavailable ({error}); this is a deterministic "
                "summary."
            ),
            "anomalies": (
                f"{anomaly_count} numeric baseline deviation candidates."
                if anomaly_count
                else ""
            ),
            "maintenance_notes": maintenance,
            "candidate_memories": "",
            "notify_owner": False,
        }

    # ------------------------------------------------------------ persistence

    @callback
    def _storage_payload(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "events": [event.as_dict() for event in self.events],
            "summaries": [summary.as_dict() for summary in self.summaries],
            "knowledge": [item.as_dict() for item in self.knowledge],
            "stay_context": self.stay_context,
            "patterns": self.patterns.as_dict(),
            "discovery": self.discovery.state,
            "discovery_activity": self.discovery.activity,
            "summary_failures": self.summary_failures,
            "last_summary_error": self.last_summary_error,
            "last_summary_error_at": self.last_summary_error_at,
            "last_anomaly_alert": (
                self.last_anomaly_alert.isoformat() if self.last_anomaly_alert else None
            ),
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return non-sensitive operational diagnostics."""
        occupancy, _, _ = self.occupancy_context()
        return {
            "property_name": self.property_name,
            "mode": self.mode,
            "status": self.status,
            "last_error": self.last_error,
            "occupancy": occupancy,
            "learning_only": self.learning_only,
            "guidance_configured": bool(self.observer_guidance),
            "guidance_length": len(self.observer_guidance),
            "tracked_entity_count": len(self._tracked),
            "label_pinned_count": len(self.discovery.index.label_monitor),
            "label_ignored_count": len(self.discovery.index.label_ignore),
            "stored_event_count": len(self.events),
            "stored_summary_count": len(self.summaries),
            "knowledge_count": len(self.knowledge),
            "learned_entity_count": self.patterns.learned_entity_count,
            "last_summary": self.summaries[-1].as_dict() if self.summaries else None,
            "has_stay_context": bool(self.stay_context),
            "last_house_log": self.last_house_log,
            "automatic_discovery": bool(self.options[CONF_AUTO_DISCOVERY]),
            "discovery": {
                key: value
                for key, value in self.discovery.response().items()
                if key != "recommendations"
            },
        }
