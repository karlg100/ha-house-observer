"""Diagnostics support for House Observer."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ALWAYS_MONITOR_DEVICES,
    CONF_ALWAYS_MONITOR_ENTITIES,
    CONF_NEVER_MONITOR_DEVICES,
    CONF_NEVER_MONITOR_ENTITIES,
)
from .observer import HouseObserver


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics without entity IDs or stored event content."""
    manager: HouseObserver = entry.runtime_data
    options = manager.options
    return {
        "observer": manager.diagnostics(),
        "override_counts": {
            key: len(options.get(key, []))
            for key in (
                CONF_ALWAYS_MONITOR_DEVICES,
                CONF_ALWAYS_MONITOR_ENTITIES,
                CONF_NEVER_MONITOR_DEVICES,
                CONF_NEVER_MONITOR_ENTITIES,
            )
        },
        "occupancy_entity_configured": bool(options.get("occupancy_entity")),
        "reservation_entity_count": len(options.get("reservation_entities", [])),
        "summary_time": str(options.get("summary_time")),
        "lookback_hours": options.get("lookback_hours"),
        "retention_days": options.get("retention_days"),
        "minimum_baseline_samples": options.get("minimum_baseline_samples"),
        "zscore_threshold": options.get("zscore_threshold"),
        "notification_configured": bool(options.get("notify_service")),
        "ai_task_configured": bool(options.get("ai_task_entity")),
        "guidance_configured": bool(manager.observer_guidance),
        "guidance_length": len(manager.observer_guidance),
    }
