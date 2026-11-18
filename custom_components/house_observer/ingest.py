"""Pure helpers that turn raw state changes into bounded observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

SAFE_ATTRIBUTES = (
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


def safe_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """Keep a deliberate small subset of useful scalar state attributes."""
    return {
        key: attributes[key]
        for key in SAFE_ATTRIBUTES
        if key in attributes
        and isinstance(attributes[key], (str, int, float, bool, type(None)))
    }


def minutes_since(since: datetime | None, now: datetime) -> int | None:
    """Return whole minutes elapsed, or None when unknown."""
    if since is None:
        return None
    return max(0, int((now - since).total_seconds() // 60))


@dataclass(slots=True)
class _Window:
    stored_at: datetime
    anomaly_at: datetime | None = None
    minimum: float | None = None
    maximum: float | None = None
    total: float = 0.0
    samples: int = 0

    def add(self, value: float) -> None:
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        self.total += value
        self.samples += 1

    def summary(self) -> dict[str, Any]:
        if self.samples < 2 or self.minimum is None or self.maximum is None:
            return {}
        return {
            "window_min": round(self.minimum, 4),
            "window_max": round(self.maximum, 4),
            "window_mean": round(self.total / self.samples, 4),
            "window_samples": self.samples,
        }


class NumericThrottle:
    """Store at most one numeric event per window without losing readings.

    Every reading still reaches the baseline learner. Readings between stored
    events are folded into window statistics attached to the next stored
    event, so a short spike remains visible. A deviation is stored right away
    unless one was already stored for that entity in the current window.
    """

    def __init__(self, window_seconds: float) -> None:
        """Initialize the throttle."""
        self.window_seconds = window_seconds
        self._windows: dict[str, _Window] = {}

    def offer(
        self, entity_id: str, when: datetime, value: float, *, anomalous: bool
    ) -> dict[str, Any] | None:
        """Return window attributes when the reading should be stored."""
        window = self._windows.get(entity_id)
        if window is None:
            self._windows[entity_id] = _Window(
                stored_at=when, anomaly_at=when if anomalous else None
            )
            return {}

        window.add(value)
        elapsed = (when - window.stored_at).total_seconds()
        store = elapsed >= self.window_seconds
        if anomalous and (
            window.anomaly_at is None
            or (when - window.anomaly_at).total_seconds() >= self.window_seconds
        ):
            store = True
        if not store:
            return None

        summary = window.summary()
        self._windows[entity_id] = _Window(
            stored_at=when,
            anomaly_at=when if anomalous else window.anomaly_at,
        )
        return summary

    def forget(self, entity_ids: set[str]) -> None:
        """Drop windows for entities that are no longer monitored."""
        for entity_id in entity_ids:
            self._windows.pop(entity_id, None)
