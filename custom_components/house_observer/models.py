"""Serializable models used by House Observer."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any


def parse_timestamp(value: Any) -> datetime | None:
    """Parse a stored ISO timestamp, returning None when invalid."""
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(slots=True)
class ObservationEvent:
    """A meaningful Home Assistant state transition."""

    timestamp: str
    entity_id: str
    name: str
    category: str
    old_state: str | None
    new_state: str
    unit: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    anomaly: dict[str, Any] | None = None
    _when: datetime | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def when(self) -> datetime | None:
        """Return the parsed timestamp, parsing it at most once."""
        if self._when is None:
            self._when = parse_timestamp(self.timestamp)
        return self._when

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "timestamp": self.timestamp,
            "entity_id": self.entity_id,
            "name": self.name,
            "category": self.category,
            "old_state": self.old_state,
            "new_state": self.new_state,
            "unit": self.unit,
            "attributes": dict(self.attributes),
            "anomaly": self.anomaly,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ObservationEvent:
        """Build an event from stored data."""
        return cls(
            timestamp=str(value["timestamp"]),
            entity_id=str(value["entity_id"]),
            name=str(value.get("name", value["entity_id"])),
            category=str(value.get("category", "other")),
            old_state=value.get("old_state"),
            new_state=str(value.get("new_state", "unknown")),
            unit=value.get("unit"),
            attributes=dict(value.get("attributes") or {}),
            anomaly=value.get("anomaly"),
        )


@dataclass(slots=True)
class ObserverSummary:
    """A generated house summary."""

    timestamp: str
    reason: str
    period_hours: int
    summary: str
    severity: str = "normal"
    confidence: float = 0.0
    observations: str = ""
    anomalies: str = ""
    maintenance_notes: str = ""
    candidate_memories: str = ""
    notify_owner: bool = False
    ai_generated: bool = False
    mode: str = ""
    occupancy: str = ""
    analysis_path: str = ""
    reply_compacted: bool = False
    occupancy_estimate: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {item.name: getattr(self, item.name) for item in fields(self)}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ObserverSummary:
        """Build a summary from stored data, ignoring unknown fields."""
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: val for key, val in value.items() if key in allowed})


@dataclass(slots=True)
class KnowledgeItem:
    """A durable owner note or confirmed property pattern."""

    note_id: str
    created: str
    category: str
    text: str
    expires: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {item.name: getattr(self, item.name) for item in fields(self)}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> KnowledgeItem:
        """Build an item from stored data."""
        return cls(
            note_id=str(value["note_id"]),
            created=str(value["created"]),
            category=str(value.get("category", "owner")),
            text=str(value.get("text", "")),
            expires=value.get("expires"),
        )
