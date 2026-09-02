"""Serializable models used by House Observer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)

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
            attributes=dict(value.get("attributes", {})),
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

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ObserverSummary:
        """Build a summary from stored data."""
        allowed = {item.name for item in cls.__dataclass_fields__.values()}
        return cls(**{key: val for key, val in value.items() if key in allowed})
