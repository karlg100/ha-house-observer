"""Pure helpers for automatic House Observer entity discovery."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

DISCOVERY_RETRY_HOURS = 24
DISCOVERY_BOOTSTRAP_MILESTONES_HOURS = (24, 72, 168)


def next_discovery_due(
    *,
    last_run: datetime,
    bootstrap_started_at: datetime,
    successful_ai_runs: int,
    ai_generated: bool,
    configured_interval_hours: int,
) -> datetime:
    """Return the next AI discovery review time."""
    if not ai_generated:
        return last_run + timedelta(hours=DISCOVERY_RETRY_HOURS)

    configured_due = last_run + timedelta(hours=configured_interval_hours)
    if 1 <= successful_ai_runs <= len(DISCOVERY_BOOTSTRAP_MILESTONES_HOURS):
        milestone_due = bootstrap_started_at + timedelta(
            hours=DISCOVERY_BOOTSTRAP_MILESTONES_HOURS[successful_ai_runs - 1]
        )
        return min(configured_due, milestone_due)
    return configured_due

DISCOVERY_CATEGORIES = {
    "access",
    "activity",
    "energy",
    "hvac",
    "network",
    "occupancy",
    "other",
    "reservation",
    "spa",
}

PRIVATE_OR_UNHELPFUL_DOMAINS = {
    "ai_task",
    "automation",
    "button",
    "camera",
    "conversation",
    "device_tracker",
    "image",
    "person",
    "proximity",
    "scene",
    "script",
    "stt",
    "tts",
    "update",
    "zone",
}

_PERSONAL_DEVICE_KEYWORDS = {
    "android phone",
    "apple watch",
    "device tracker",
    "ipad",
    "iphone",
    "pixel phone",
    "smartphone",
}

_DOMAIN_SCORES = {
    "alarm_control_panel": 75,
    "binary_sensor": 35,
    "calendar": 25,
    "climate": 85,
    "cover": 55,
    "fan": 30,
    "humidifier": 55,
    "lock": 80,
    "media_player": 15,
    "select": 20,
    "sensor": 20,
    "switch": 25,
    "vacuum": 35,
    "water_heater": 80,
    "weather": 55,
}

_DEVICE_CLASS_SCORES = {
    "battery": 20,
    "carbon_monoxide": 40,
    "connectivity": 35,
    "door": 40,
    "energy": 35,
    "gas": 40,
    "heat": 30,
    "humidity": 30,
    "moisture": 40,
    "motion": 30,
    "occupancy": 35,
    "opening": 35,
    "power": 35,
    "presence": 35,
    "problem": 40,
    "smoke": 40,
    "temperature": 35,
    "window": 30,
}

_KEYWORD_SCORES = {
    "door": 20,
    "entry": 15,
    "garage": 20,
    "heat": 15,
    "hvac": 25,
    "internet": 40,
    "leak": 30,
    "lock": 20,
    "motion": 10,
    "occupancy": 15,
    "outage": 25,
    "power": 15,
    "router": 40,
    "spa": 30,
    "starlink": 50,
    "temperature": 15,
    "thermostat": 25,
    "water": 15,
}


@dataclass(slots=True)
class DiscoveryCandidate:
    """One entity the discovery system may recommend."""

    entity_id: str
    name: str
    state: str
    domain: str
    device_class: str | None = None
    unit: str | None = None
    entity_category: str | None = None
    device_id: str | None = None
    device_name: str | None = None
    area_id: str | None = None
    area_name: str = "Unassigned"
    activity_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)

    def prompt_dict(self) -> dict[str, Any]:
        """Return the compact fields useful to an AI model."""
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "device": self.device_name,
            "domain": self.domain,
            "device_class": self.device_class,
            "unit": self.unit,
            "state": self.state[:80],
            "recent_changes": self.activity_count,
            "local_relevance": relevance_score(self),
        }


def relevance_score(candidate: DiscoveryCandidate) -> int:
    """Return a conservative local relevance score from zero through 100."""
    if candidate.domain in PRIVATE_OR_UNHELPFUL_DOMAINS:
        return 0
    if candidate.entity_category == "config":
        return 0

    searchable = (
        f"{candidate.name} {candidate.entity_id} {candidate.device_name or ''}"
    ).lower()
    if any(keyword in searchable for keyword in _PERSONAL_DEVICE_KEYWORDS):
        return 0

    score = _DOMAIN_SCORES.get(candidate.domain, 10)
    if candidate.device_class:
        score += _DEVICE_CLASS_SCORES.get(candidate.device_class, 0)
    score += max(
        (
            points
            for keyword, points in _KEYWORD_SCORES.items()
            if keyword in searchable
        ),
        default=0,
    )
    if candidate.entity_category == "diagnostic":
        score -= 15
    if candidate.activity_count:
        score += min(15, round(math.log2(candidate.activity_count + 1) * 3))
    return max(0, min(100, score))


def eligible_candidates(
    candidates: list[DiscoveryCandidate], *, limit: int = 400
) -> list[DiscoveryCandidate]:
    """Keep a bounded, area-balanced inventory for AI evaluation."""
    by_area: dict[str, list[DiscoveryCandidate]] = {}
    for candidate in candidates:
        if relevance_score(candidate) < 15:
            continue
        by_area.setdefault(candidate.area_name, []).append(candidate)

    ranked_areas: dict[str, list[DiscoveryCandidate]] = {}
    for area_name in sorted(by_area):
        ranked_areas[area_name] = sorted(
            by_area[area_name],
            key=lambda item: (-relevance_score(item), item.entity_id),
        )[:60]

    selected: list[DiscoveryCandidate] = []
    for position in range(60):
        for area_name in sorted(ranked_areas):
            area_candidates = ranked_areas[area_name]
            if position < len(area_candidates):
                selected.append(area_candidates[position])
                if len(selected) == limit:
                    return selected
    return selected


def default_category(candidate: DiscoveryCandidate) -> str:
    """Infer an operational category when the AI does not supply one."""
    text = f"{candidate.entity_id} {candidate.name}".lower()
    if candidate.domain == "lock" or any(
        word in text for word in ("door", "window", "garage", "entry")
    ):
        return "access"
    if any(word in text for word in ("spa", "hot_tub", "hot tub")):
        return "spa"
    if candidate.domain in {"climate", "humidifier", "water_heater"} or any(
        word in text for word in ("hvac", "thermostat", "heat", "temperature")
    ):
        return "hvac"
    if candidate.device_class in {"energy", "power"}:
        return "energy"
    if any(word in text for word in ("internet", "router", "starlink", "wan")):
        return "network"
    if candidate.device_class in {"occupancy", "presence"}:
        return "occupancy"
    if candidate.device_class == "motion" or candidate.domain == "media_player":
        return "activity"
    if candidate.domain == "calendar":
        return "reservation"
    return "other"


def parse_recommendations(
    text: Any,
    candidates: list[DiscoveryCandidate],
    *,
    confidence: float = 0.0,
) -> list[dict[str, Any]]:
    """Validate line-based AI recommendations against the supplied inventory."""
    known = {candidate.entity_id: candidate for candidate in candidates}
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        bounded_confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        bounded_confidence = 0.0

    for raw_line in str(text or "").splitlines():
        line = raw_line.strip().strip("`*- ")
        match = re.match(
            r"^(?P<entity>[a-z0-9_]+\.[a-z0-9_]+)\s*\|\s*"
            r"(?P<category>[a-z_]+)\s*\|\s*(?P<reason>.+)$",
            line,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        entity_id = match.group("entity").lower()
        candidate = known.get(entity_id)
        if candidate is None or entity_id in seen:
            continue
        category = match.group("category").lower()
        if category not in DISCOVERY_CATEGORIES:
            category = default_category(candidate)
        reason = match.group("reason").strip()[:300]
        output.append(
            {
                **candidate.as_dict(),
                "category": category,
                "reason": reason,
                "confidence": bounded_confidence,
                "source": "ai",
            }
        )
        seen.add(entity_id)
    return output


def fallback_recommendations(
    candidates: list[DiscoveryCandidate],
) -> list[dict[str, Any]]:
    """Recommend only high-confidence candidates when AI is unavailable."""
    return [
        {
            **candidate.as_dict(),
            "category": default_category(candidate),
            "reason": "Selected by conservative local relevance rules.",
            "confidence": 0.5,
            "source": "local",
        }
        for candidate in candidates
        if relevance_score(candidate) >= 70
    ]
