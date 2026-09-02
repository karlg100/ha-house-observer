"""Small, explainable baseline learner for House Observer."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class OnlineStats:
    """Numerically stable running statistics."""

    count: int = 0
    mean: float = 0.0
    m2: float = 0.0
    minimum: float | None = None
    maximum: float | None = None

    @property
    def variance(self) -> float:
        """Return sample variance."""
        return self.m2 / (self.count - 1) if self.count > 1 else 0.0

    @property
    def standard_deviation(self) -> float:
        """Return sample standard deviation."""
        return math.sqrt(max(0.0, self.variance))

    def z_score(self, value: float) -> float | None:
        """Return the value's z-score against the existing baseline."""
        deviation = self.standard_deviation
        if self.count < 2 or deviation < 1e-9:
            return None
        return (value - self.mean) / deviation

    def update(self, value: float) -> None:
        """Add a value to the baseline."""
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        delta2 = value - self.mean
        self.m2 += delta * delta2
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    def as_dict(self) -> dict[str, Any]:
        """Serialize statistics."""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> OnlineStats:
        """Restore serialized statistics."""
        return cls(
            count=int(value.get("count", 0)),
            mean=float(value.get("mean", 0.0)),
            m2=float(value.get("m2", 0.0)),
            minimum=value.get("minimum"),
            maximum=value.get("maximum"),
        )


def numeric_value(state: str | int | float | None) -> float | None:
    """Convert a finite numeric state, rejecting unknown values."""
    if state is None or isinstance(state, bool):
        return None
    try:
        value = float(state)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


class PatternEngine:
    """Learn per-entity state and numeric baselines."""

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        *,
        minimum_samples: int = 30,
        zscore_threshold: float = 3.5,
    ) -> None:
        """Initialize the engine."""
        self.minimum_samples = minimum_samples
        self.zscore_threshold = zscore_threshold
        self.entities: dict[str, dict[str, Any]] = {}
        for entity_id, stored in (data or {}).get("entities", {}).items():
            restored = dict(stored)
            restored["numeric"] = OnlineStats.from_dict(stored.get("numeric", {}))
            self.entities[entity_id] = restored

    def observe(
        self,
        entity_id: str,
        state: str,
        observed_at: datetime,
    ) -> dict[str, Any] | None:
        """Learn from a state and return a candidate numeric anomaly."""
        record = self.entities.setdefault(
            entity_id,
            {
                "numeric": OnlineStats(),
                "state_counts": {},
                "hour_counts": [0] * 24,
                "observations": 0,
                "last_state": None,
            },
        )
        stats: OnlineStats = record["numeric"]
        value = numeric_value(state)
        anomaly: dict[str, Any] | None = None

        if value is not None and stats.count >= self.minimum_samples:
            zscore = stats.z_score(value)
            if zscore is not None and abs(zscore) >= self.zscore_threshold:
                anomaly = {
                    "kind": "numeric_deviation",
                    "value": round(value, 4),
                    "baseline_mean": round(stats.mean, 4),
                    "baseline_standard_deviation": round(stats.standard_deviation, 4),
                    "z_score": round(zscore, 2),
                    "samples": stats.count,
                    "severity": (
                        "action"
                        if abs(zscore) >= self.zscore_threshold * 1.75
                        else "watch"
                    ),
                }
            elif zscore is None:
                tolerance = max(0.01, abs(stats.mean) * 0.001)
                if abs(value - stats.mean) > tolerance:
                    anomaly = {
                        "kind": "constant_baseline_break",
                        "value": round(value, 4),
                        "baseline_mean": round(stats.mean, 4),
                        "baseline_standard_deviation": round(
                            stats.standard_deviation, 4
                        ),
                        "samples": stats.count,
                        "severity": "watch",
                    }
        if value is not None:
            stats.update(value)

        counts = record["state_counts"]
        counts[state] = int(counts.get(state, 0)) + 1
        record["hour_counts"][observed_at.hour] += 1
        record["observations"] = int(record.get("observations", 0)) + 1
        record["last_state"] = state
        record["last_observed"] = observed_at.isoformat()
        return anomaly

    @property
    def learned_entity_count(self) -> int:
        """Return entities that have enough observations for a baseline."""
        return sum(
            int(record.get("observations", 0)) >= self.minimum_samples
            for record in self.entities.values()
        )

    def baseline_context(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return a compact, human- and model-readable baseline snapshot."""
        output: list[dict[str, Any]] = []
        ranked = sorted(
            self.entities.items(),
            key=lambda item: int(item[1].get("observations", 0)),
            reverse=True,
        )
        for entity_id, record in ranked[:limit]:
            stats: OnlineStats = record["numeric"]
            item: dict[str, Any] = {
                "entity_id": entity_id,
                "observations": int(record.get("observations", 0)),
                "common_states": sorted(
                    record.get("state_counts", {}).items(),
                    key=lambda pair: pair[1],
                    reverse=True,
                )[:4],
                "active_hours": [
                    pair
                    for pair in sorted(
                        enumerate(record.get("hour_counts", [0] * 24)),
                        key=lambda pair: pair[1],
                        reverse=True,
                    )
                    if pair[1] > 0
                ][:4],
            }
            if stats.count:
                item["numeric"] = {
                    "samples": stats.count,
                    "mean": round(stats.mean, 4),
                    "standard_deviation": round(stats.standard_deviation, 4),
                    "minimum": stats.minimum,
                    "maximum": stats.maximum,
                }
            output.append(item)
        return output

    def as_dict(self) -> dict[str, Any]:
        """Serialize all learned patterns."""
        entities: dict[str, Any] = {}
        for entity_id, record in self.entities.items():
            stored = dict(record)
            stored["numeric"] = record["numeric"].as_dict()
            entities[entity_id] = stored
        return {"entities": entities}
