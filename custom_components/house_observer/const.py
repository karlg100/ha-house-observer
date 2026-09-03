"""Constants for House Observer."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "house_observer"
NAME: Final = "House Observer"
PLATFORMS: Final = [Platform.SENSOR]

CONF_PROPERTY_NAME: Final = "property_name"
CONF_ACTIVITY_ENTITIES: Final = "activity_entities"
CONF_ACCESS_ENTITIES: Final = "access_entities"
CONF_OCCUPANCY_ENTITIES: Final = "occupancy_entities"
CONF_SPA_ENTITIES: Final = "spa_entities"
CONF_HVAC_ENTITIES: Final = "hvac_entities"
CONF_NETWORK_ENTITIES: Final = "network_entities"
CONF_ENERGY_ENTITIES: Final = "energy_entities"
CONF_RESERVATION_ENTITIES: Final = "reservation_entities"
CONF_AI_TASK_ENTITY: Final = "ai_task_entity"
CONF_SUMMARY_TIME: Final = "summary_time"
CONF_LOOKBACK_HOURS: Final = "lookback_hours"
CONF_RETENTION_DAYS: Final = "retention_days"
CONF_LEARNING_ONLY: Final = "learning_only"
CONF_NOTIFY_SERVICE: Final = "notify_service"
CONF_NOTIFY_DAILY: Final = "notify_daily_summary"
CONF_MIN_SAMPLES: Final = "minimum_baseline_samples"
CONF_ZSCORE_THRESHOLD: Final = "zscore_threshold"
CONF_AUTO_DISCOVERY: Final = "auto_discovery"
CONF_DISCOVERY_INTERVAL_HOURS: Final = "discovery_interval_hours"
CONF_ALWAYS_MONITOR_DEVICES: Final = "always_monitor_devices"
CONF_NEVER_MONITOR_DEVICES: Final = "never_monitor_devices"
CONF_ALWAYS_MONITOR_ENTITIES: Final = "always_monitor_entities"
CONF_NEVER_MONITOR_ENTITIES: Final = "never_monitor_entities"

ENTITY_GROUPS: Final = (
    CONF_ACTIVITY_ENTITIES,
    CONF_ACCESS_ENTITIES,
    CONF_OCCUPANCY_ENTITIES,
    CONF_SPA_ENTITIES,
    CONF_HVAC_ENTITIES,
    CONF_NETWORK_ENTITIES,
    CONF_ENERGY_ENTITIES,
    CONF_RESERVATION_ENTITIES,
)

GROUP_LABELS: Final = {
    CONF_ACTIVITY_ENTITIES: "activity",
    CONF_ACCESS_ENTITIES: "access",
    CONF_OCCUPANCY_ENTITIES: "occupancy",
    CONF_SPA_ENTITIES: "spa",
    CONF_HVAC_ENTITIES: "hvac",
    CONF_NETWORK_ENTITIES: "network",
    CONF_ENERGY_ENTITIES: "energy",
    CONF_RESERVATION_ENTITIES: "reservation",
}

DEFAULT_PROPERTY_NAME: Final = "Home"
DEFAULT_SUMMARY_TIME: Final = "08:00:00"
DEFAULT_LOOKBACK_HOURS: Final = 24
DEFAULT_RETENTION_DAYS: Final = 45
DEFAULT_LEARNING_ONLY: Final = True
DEFAULT_NOTIFY_DAILY: Final = False
DEFAULT_MIN_SAMPLES: Final = 30
DEFAULT_ZSCORE_THRESHOLD: Final = 3.5
DEFAULT_AUTO_DISCOVERY: Final = True
DEFAULT_DISCOVERY_INTERVAL_HOURS: Final = 168
DEFAULT_EVENT_DEBOUNCE_SECONDS: Final = 300
DEFAULT_MAX_EVENTS: Final = 5000
DEFAULT_MAX_SUMMARIES: Final = 120
DEFAULT_ANOMALY_COOLDOWN_MINUTES: Final = 30

STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = f"{DOMAIN}."

SERVICE_GENERATE_SUMMARY: Final = "generate_summary"
SERVICE_RECORD_NOTE: Final = "record_note"
SERVICE_SET_STAY_CONTEXT: Final = "set_stay_context"
SERVICE_DISCOVER_ENTITIES: Final = "discover_entities"

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_HOURS: Final = "hours"
ATTR_NOTE: Final = "note"
ATTR_CATEGORY: Final = "category"
ATTR_RESERVATION_ID: Final = "reservation_id"
ATTR_LABEL: Final = "label"
ATTR_GUEST_COUNT: Final = "guest_count"
ATTR_PET_COUNT: Final = "pet_count"
ATTR_CHECK_IN: Final = "check_in"
ATTR_CHECK_OUT: Final = "check_out"
ATTR_CLEAR: Final = "clear"

SEVERITIES: Final = ("normal", "note", "watch", "action")
