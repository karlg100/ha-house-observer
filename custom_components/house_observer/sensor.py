"""Sensors exposed by House Observer."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MODE_RENTAL, NAME, OCCUPANCY_STATES, SEVERITIES
from .observer import HouseObserver


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up observer sensors."""
    manager: HouseObserver = entry.runtime_data
    async_add_entities(
        [
            ObserverStatusSensor(manager),
            ObserverConditionSensor(manager),
            ObserverOccupancySensor(manager),
            ObserverEventsSensor(manager),
            ObserverAnomaliesSensor(manager),
            ObserverBaselinesSensor(manager),
            ObserverDiscoverySensor(manager),
            ObserverSummarySensor(manager),
            ObserverKnowledgeSensor(manager),
        ]
    )


class ObserverSensorBase(SensorEntity):
    """Base class for observer sensors."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: HouseObserver, key: str) -> None:
        """Initialize a sensor."""
        self.manager = manager
        self._attr_unique_id = f"{manager.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, manager.entry.entry_id)},
            name=manager.property_name,
            manufacturer=NAME,
            model="Local operational observer",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to memory updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.manager.async_add_listener(self._async_memory_updated)
        )

    @callback
    def _async_memory_updated(self) -> None:
        """Write a refreshed entity state."""
        self.async_write_ha_state()


class ObserverStatusSensor(ObserverSensorBase):
    """Operational health: learning, active, or error."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["learning", "active", "error"]
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: HouseObserver) -> None:
        super().__init__(manager, "status")

    @property
    def native_value(self) -> str:
        return self.manager.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        error = self.manager.last_error or {}
        return {
            "mode": self.manager.mode,
            "learning_only": self.manager.learning_only,
            "last_error": error.get("message"),
            "last_error_source": error.get("source"),
            "last_error_at": error.get("at"),
            "analysis_agent_error": self.manager.last_agent_error,
            "house_log": self.manager.last_house_log,
            "ai_output_limit_tokens": self.manager.output_limit,
            "tracked_entities": len(self.manager.tracked_entities),
            "retained_events": len(self.manager.events),
            "retained_summaries": len(self.manager.summaries),
            "guidance_configured": bool(self.manager.observer_guidance),
        }


class ObserverConditionSensor(ObserverSensorBase):
    """The latest summary's severity, independent of learning mode."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(SEVERITIES)

    def __init__(self, manager: HouseObserver) -> None:
        super().__init__(manager, "condition")

    @property
    def native_value(self) -> str | None:
        if not self.manager.summaries:
            return None
        return self.manager.summaries[-1].severity

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.manager.summaries:
            return {}
        summary = self.manager.summaries[-1]
        return {
            "summary_at": summary.timestamp,
            "reason": summary.reason,
            "ai_generated": summary.ai_generated,
        }


class ObserverOccupancySensor(ObserverSensorBase):
    """Resolved occupancy: occupied, vacant, or unknown."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(OCCUPANCY_STATES)

    def __init__(self, manager: HouseObserver) -> None:
        # The key predates occupancy resolution; keeping it preserves entity IDs.
        super().__init__(manager, "active_stay")

    @property
    def native_value(self) -> str:
        occupancy, _, _ = self.manager.occupancy_context()
        return str(occupancy["state"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        occupancy, stay, _ = self.manager.occupancy_context()
        attributes: dict[str, Any] = {
            "source": occupancy.get("source"),
            "mode": self.manager.mode,
        }
        if self.manager.mode == MODE_RENTAL:
            attributes["next_arrival"] = (occupancy.get("next_arrival") or {}).get(
                "start_local"
            )
            if stay is not None:
                attributes.update(
                    {
                        "stay_check_in": stay.get("start_local"),
                        "stay_check_out": stay.get("end_local"),
                        "guest_count": stay.get("guest_count"),
                        "pet_count": stay.get("pet_count"),
                    }
                )
        return attributes


class ObserverEventsSensor(ObserverSensorBase):
    """Meaningful events during the last day."""

    _attr_native_unit_of_measurement = "events"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: HouseObserver) -> None:
        super().__init__(manager, "events_24h")

    @property
    def native_value(self) -> int:
        return len(self.manager.events_since(24))


class ObserverAnomaliesSensor(ObserverSensorBase):
    """Baseline deviation candidates during the last day."""

    _attr_native_unit_of_measurement = "anomalies"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: HouseObserver) -> None:
        super().__init__(manager, "anomalies_24h")

    @property
    def native_value(self) -> int:
        return sum(event.anomaly is not None for event in self.manager.events_since(24))


class ObserverBaselinesSensor(ObserverSensorBase):
    """Count of entities with learned baselines."""

    _attr_native_unit_of_measurement = "entities"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: HouseObserver) -> None:
        super().__init__(manager, "learned_baselines")

    @property
    def native_value(self) -> int:
        return self.manager.patterns.learned_entity_count


class ObserverDiscoverySensor(ObserverSensorBase):
    """Monitored entity count plus discovery status and recommendations."""

    _attr_native_unit_of_measurement = "entities"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"recommendations"})

    def __init__(self, manager: HouseObserver) -> None:
        super().__init__(manager, "discovered_devices")

    @property
    def native_value(self) -> int:
        return len(self.manager.tracked_entities)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        result = self.manager.discovery.response()
        result["recommendations"] = [
            {
                "entity_id": item.get("entity_id"),
                "name": item.get("name"),
                "device": item.get("device_name"),
                "area": item.get("area_name"),
                "category": item.get("category"),
                "reason": item.get("reason"),
                "source": item.get("source"),
            }
            for item in result["recommendations"][:50]
        ]
        return result


class ObserverSummarySensor(ObserverSensorBase):
    """Latest generated summary."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset(
        {
            "summary",
            "observations",
            "anomalies",
            "maintenance_notes",
            "candidate_memories",
        }
    )

    def __init__(self, manager: HouseObserver) -> None:
        super().__init__(manager, "last_summary")

    @property
    def native_value(self) -> datetime | None:
        if not self.manager.summaries:
            return None
        return dt_util.parse_datetime(self.manager.summaries[-1].timestamp)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.manager.summaries:
            return {}
        summary = self.manager.summaries[-1]
        return {
            "summary": summary.summary,
            "severity": summary.severity,
            "confidence": summary.confidence,
            "observations": summary.observations,
            "anomalies": summary.anomalies,
            "maintenance_notes": summary.maintenance_notes,
            "candidate_memories": summary.candidate_memories,
            "reason": summary.reason,
            "period_hours": summary.period_hours,
            "mode": summary.mode,
            "occupancy": summary.occupancy,
            "ai_generated": summary.ai_generated,
            "analysis_path": summary.analysis_path,
            "reply_compacted": summary.reply_compacted,
        }


class ObserverKnowledgeSensor(ObserverSensorBase):
    """Owner notes and confirmed patterns every summary receives."""

    _attr_native_unit_of_measurement = "notes"
    _unrecorded_attributes = frozenset({"notes"})

    def __init__(self, manager: HouseObserver) -> None:
        super().__init__(manager, "property_notes")

    @property
    def native_value(self) -> int:
        return len(self.manager.knowledge)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "notes": [
                {
                    "note_id": item.note_id,
                    "recorded": item.created,
                    "category": item.category,
                    "note": item.text,
                    "expires": item.expires,
                }
                for item in self.manager.knowledge[-50:]
            ]
        }
