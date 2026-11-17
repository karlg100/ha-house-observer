"""Small, explainable baseline learner for House Observer.

Each numeric entity keeps an overall baseline plus one baseline per part of
the day. Older readings fade with a configurable half-life, so seasonal or
equipment changes are absorbed instead of skewing comparisons forever.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

DAY_PARTS = ("night", "morning", "afternoon", "evening")


def day_part(when: datetime) -> str:
    """Return night (0-6), morning (6-12), afternoon (12-18), or evening."""
    return DAY_PARTS[when.hour // 6]


@dataclass(slots=True)
class OnlineStats:
    """Exponentially decaying running statistics (weighted Welford)."""

    count: int = 0
    weight: float = 0.0
    mean: float = 0.0
    m2: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    last_update: str | None = None

    @property
    def variance(self) -> float:
        """Return the reliability-weighted sample variance."""
        return self.m2 / (self.weight - 1) if self.weight > 1 else 0.0

    @property
    def standard_deviation(self) -> float:
        """Return the sample standard deviation."""
        return math.sqrt(max(0.0, self.variance))

    def z_score(self, value: float) -> float | None:
        """Return the value's z-score against the existing baseline."""
        deviation = self.standard_deviation
        if self.weight < 2 or deviation < 1e-9:
            return None
        return (value - self.mean) / deviation

    def decay(self, when: datetime | None, half_life_days: float | None) -> None:
        """Fade older readings according to the elapsed time."""
        if when is None or not half_life_days or self.last_update is None:
            return
        try:
            previous = datetime.fromisoformat(self.last_update)
            elapsed = (when - previous).total_seconds()
        except (TypeError, ValueError):
            return
        if elapsed <= 0:
            return
        factor = 0.5 ** (elapsed / (half_life_days * 86400))
        self.weight *= factor
        self.m2 *= factor

    def update(
        self,
        value: float,
        when: datetime | None = None,
        half_life_days: float | None = None,
    ) -> None:
        """Add a value to the baseline."""
        self.decay(when, half_life_days)
        self.count += 1
        self.weight += 1.0
        delta = value - self.mean
        self.mean += delta / self.weight
        self.m2 += delta * (value - self.mean)
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        if when is not None:
            self.last_update = when.isoformat()

    def snapshot(self) -> dict[str, Any]:
        """Return a compact model-readable baseline."""
        return {
            "samples": self.count,
            "effective_samples": round(self.weight, 1),
            "mean": round(self.mean, 4),
            "standard_deviation": round(self.standard_deviation, 4),
            "observed_min": self.minimum,
            "observed_max": self.maximum,
        }

    def as_dict(self) -> dict[str, Any]:
        """Serialize statistics."""
        return {
            "count": self.count,
            "weight": self.weight,
            "mean": self.mean,
            "m2": self.m2,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "last_update": self.last_update,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> OnlineStats:
        """Restore serialized statistics, including pre-decay records."""
        value = value or {}
        count = int(value.get("count", 0))
        return cls(
            count=count,
            weight=float(value.get("weight", count)),
            mean=float(value.get("mean", 0.0)),
            m2=float(value.get("m2", 0.0)),
            minimum=value.get("minimum"),
            maximum=value.get("maximum"),
            last_update=value.get("last_update"),
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
        half_life_days: float | None = None,
    ) -> None:
        """Initialize the engine."""
        self.minimum_samples = minimum_samples
        self.zscore_threshold = zscore_threshold
        self.half_life_days = half_life_days
        self.entities: dict[str, dict[str, Any]] = {}
        for entity_id, stored in (data or {}).get("entities", {}).items():
            restored = dict(stored)
            restored["numeric"] = OnlineStats.from_dict(stored.get("numeric"))
            restored["parts"] = {
                part: OnlineStats.from_dict(value)
                for part, value in (stored.get("parts") or {}).items()
                if part in DAY_PARTS
            }
            self.entities[entity_id] = restored

    def _baseline(
        self, record: dict[str, Any], part: str
    ) -> tuple[str, OnlineStats] | None:
        """Prefer the time-of-day baseline, falling back to the overall one."""
        part_stats: OnlineStats | None = record["parts"].get(part)
        if part_stats is not None and part_stats.weight >= self.minimum_samples:
            return part, part_stats
        overall: OnlineStats = record["numeric"]
        if overall.weight >= self.minimum_samples:
            return "overall", overall
        return None

    def evaluate(
        self, entity_id: str, value: float, observed_at: datetime
    ) -> dict[str, Any] | None:
        """Compare a value with the learned baseline without learning from it."""
        record = self.entities.get(entity_id)
        if record is None:
            return None
        chosen = self._baseline(record, day_part(observed_at))
        if chosen is None:
            return None
        baseline_name, stats = chosen
        common = {
            "value": round(value, 4),
            "baseline": baseline_name,
            "baseline_mean": round(stats.mean, 4),
            "baseline_standard_deviation": round(stats.standard_deviation, 4),
            "samples": stats.count,
        }
        zscore = stats.z_score(value)
        if zscore is not None:
            if abs(zscore) < self.zscore_threshold:
                return None
            return {
                "kind": "numeric_deviation",
                **common,
                "z_score": round(zscore, 2),
                "severity": (
                    "action" if abs(zscore) >= self.zscore_threshold * 1.75 else "watch"
                ),
            }
        tolerance = max(0.01, abs(stats.mean) * 0.001)
        if abs(value - stats.mean) > tolerance:
            return {"kind": "constant_baseline_break", **common, "severity": "watch"}
        return None

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
                "parts": {},
                "state_counts": {},
                "hour_counts": [0] * 24,
                "observations": 0,
                "last_state": None,
            },
        )
        value = numeric_value(state)
        anomaly: dict[str, Any] | None = None
        if value is not None:
            anomaly = self.evaluate(entity_id, value, observed_at)
            record["numeric"].update(value, observed_at, self.half_life_days)
            part = day_part(observed_at)
            record["parts"].setdefault(part, OnlineStats()).update(
                value, observed_at, self.half_life_days
            )
        else:
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

    def baseline_context(
        self, limit: int = 50, entity_ids: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """Return a compact, human- and model-readable baseline snapshot."""
        output: list[dict[str, Any]] = []
        records = [
            item
            for item in self.entities.items()
            if entity_ids is None or item[0] in entity_ids
        ]
        ranked = sorted(
            records,
            key=lambda item: int(item[1].get("observations", 0)),
            reverse=True,
        )
        for entity_id, record in ranked[:limit]:
            item: dict[str, Any] = {
                "entity_id": entity_id,
                "observations": int(record.get("observations", 0)),
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
            if common := sorted(
                record.get("state_counts", {}).items(),
                key=lambda pair: pair[1],
                reverse=True,
            )[:4]:
                item["common_states"] = common
            stats: OnlineStats = record["numeric"]
            if stats.count:
                item["numeric"] = stats.snapshot()
                parts = {
                    part: {
                        "mean": round(part_stats.mean, 4),
                        "standard_deviation": round(part_stats.standard_deviation, 4),
                        "samples": part_stats.count,
                    }
                    for part in DAY_PARTS
                    if (part_stats := record["parts"].get(part)) is not None
                    and part_stats.count
                }
                if parts:
                    item["by_time_of_day"] = parts
            output.append(item)
        return output

    def as_dict(self) -> dict[str, Any]:
        """Serialize all learned patterns."""
        entities: dict[str, Any] = {}
        for entity_id, record in self.entities.items():
            stored = dict(record)
            stored["numeric"] = record["numeric"].as_dict()
            stored["parts"] = {
                part: stats.as_dict() for part, stats in record["parts"].items()
            }
            entities[entity_id] = stored
        return {"entities": entities}
