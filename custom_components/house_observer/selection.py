"""Decide which entities are monitored and what each one means.

Monitored = discovered + pinned - ignored. Pins and ignores come from the
integration options or from Home Assistant labels on entities and devices.
Ignores always win.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .const import CATEGORIES, LABEL_CATEGORY_PREFIX, LABEL_IGNORE, LABEL_MONITOR

_LABEL_CATEGORIES = {f"{LABEL_CATEGORY_PREFIX}{name}": name for name in CATEGORIES}


@dataclass(slots=True)
class RegistryIndex:
    """Registry facts gathered once per refresh."""

    entity_devices: dict[str, str | None] = field(default_factory=dict)
    device_entities: dict[str, list[str]] = field(default_factory=dict)
    label_monitor: set[str] = field(default_factory=set)
    label_ignore: set[str] = field(default_factory=set)
    label_categories: dict[str, str] = field(default_factory=dict)


def labels_to_flags(labels: Iterable[str]) -> tuple[bool, bool, str | None]:
    """Return (monitor, ignore, category) for a set of label IDs."""
    label_set = set(labels)
    category = next(
        (
            _LABEL_CATEGORIES[label]
            for label in sorted(label_set)
            if label in _LABEL_CATEGORIES
        ),
        None,
    )
    return (
        LABEL_MONITOR in label_set or category is not None,
        LABEL_IGNORE in label_set,
        category,
    )


@dataclass(slots=True)
class SelectionInputs:
    """Owner choices that shape the monitored set."""

    auto_discovery: bool
    recommendations: list[dict[str, Any]]
    pinned_entities: Iterable[str] = ()
    pinned_devices: Iterable[str] = ()
    ignored_entities: Iterable[str] = ()
    ignored_devices: Iterable[str] = ()


def effective_entities(index: RegistryIndex, inputs: SelectionInputs) -> list[str]:
    """Return the sorted monitored entity set."""
    entities: set[str] = set(inputs.pinned_entities) | index.label_monitor
    if inputs.auto_discovery:
        entities.update(
            str(item["entity_id"])
            for item in inputs.recommendations
            if item.get("entity_id")
        )
    for device_id in inputs.pinned_devices:
        entities.update(index.device_entities.get(device_id, ()))

    ignored_devices = set(inputs.ignored_devices)
    ignored = set(inputs.ignored_entities) | index.label_ignore
    return sorted(
        entity_id
        for entity_id in entities
        if entity_id not in ignored
        and (
            (device_id := index.entity_devices.get(entity_id)) is None
            or device_id not in ignored_devices
        )
    )


def category_for(
    entity_id: str,
    *,
    index: RegistryIndex,
    overrides: dict[str, str],
    recommendations: list[dict[str, Any]],
    fallback: str,
) -> str:
    """Resolve an entity's category: label, then legacy override, then AI."""
    if category := index.label_categories.get(entity_id):
        return category
    if (category := overrides.get(entity_id)) in CATEGORIES:
        return str(category)
    for item in recommendations:
        if item.get("entity_id") == entity_id:
            return str(item.get("category") or fallback)
    return fallback
