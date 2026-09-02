"""Diagnostics support for House Observer."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import ENTITY_GROUPS
from .observer import HouseObserver


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics without entity IDs or stored event content."""
    manager: HouseObserver = entry.runtime_data
    options = manager.options
    return {
        "observer": manager.diagnostics(),
        "entity_group_counts": {
            group: len(options.get(group, [])) for group in ENTITY_GROUPS
        },
        "summary_time": str(options.get("summary_time")),
        "lookback_hours": options.get("lookback_hours"),
        "retention_days": options.get("retention_days"),
        "minimum_baseline_samples": options.get("minimum_baseline_samples"),
        "zscore_threshold": options.get("zscore_threshold"),
        "notification_configured": bool(options.get("notify_service")),
        "ai_task_configured": bool(options.get("ai_task_entity")),
    }
