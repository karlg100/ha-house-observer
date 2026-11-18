"""Constants for House Observer."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "house_observer"
NAME: Final = "House Observer"

# Config entry data
CONF_PROPERTY_NAME: Final = "property_name"
DATA_CATEGORY_OVERRIDES: Final = "category_overrides"

# Observer mode
CONF_MODE: Final = "observer_mode"
MODE_HOME: Final = "home"
MODE_RENTAL: Final = "rental"
MODES: Final = (MODE_HOME, MODE_RENTAL)
DEFAULT_MODE: Final = MODE_HOME

# Monitoring
CONF_AUTO_DISCOVERY: Final = "auto_discovery"
CONF_DISCOVERY_INTERVAL_HOURS: Final = "discovery_interval_hours"
CONF_ALWAYS_MONITOR_DEVICES: Final = "always_monitor_devices"
CONF_NEVER_MONITOR_DEVICES: Final = "never_monitor_devices"
CONF_ALWAYS_MONITOR_ENTITIES: Final = "always_monitor_entities"
CONF_NEVER_MONITOR_ENTITIES: Final = "never_monitor_entities"
CONF_OCCUPANCY_ENTITY: Final = "occupancy_entity"
CONF_RESERVATION_ENTITIES: Final = "reservation_entities"

# Analysis
CONF_AI_TASK_ENTITY: Final = "ai_task_entity"
CONF_ANALYSIS_AGENT: Final = "analysis_agent"
CONF_OBSERVER_GUIDANCE: Final = "observer_guidance"
CONF_SUMMARY_TIME: Final = "summary_time"
CONF_LOOKBACK_HOURS: Final = "lookback_hours"

# Learning
CONF_LEARNING_ONLY: Final = "learning_only"
CONF_MIN_SAMPLES: Final = "minimum_baseline_samples"
CONF_ZSCORE_THRESHOLD: Final = "zscore_threshold"
CONF_RETENTION_DAYS: Final = "retention_days"

# Notifications
CONF_NOTIFY_SERVICE: Final = "notify_service"
CONF_NOTIFY_DAILY: Final = "notify_daily_summary"

# Category pickers used before 0.3.0. They are migrated into pins plus
# category overrides and are no longer offered in the UI.
LEGACY_GROUP_CATEGORIES: Final = {
    "activity_entities": "activity",
    "access_entities": "access",
    "occupancy_entities": "occupancy",
    "spa_entities": "spa",
    "hvac_entities": "hvac",
    "network_entities": "network",
    "energy_entities": "energy",
}

CATEGORIES: Final = (
    "access",
    "activity",
    "occupancy",
    "hvac",
    "water",
    "energy",
    "appliance",
    "spa",
    "network",
    "reservation",
    "other",
)

# Home Assistant labels that steer monitoring. Labels are matched by label ID,
# which Home Assistant derives from the label name ("Observer water" ->
# "observer_water").
LABEL_MONITOR: Final = "observer"
LABEL_IGNORE: Final = "observer_ignore"
LABEL_CATEGORY_PREFIX: Final = "observer_"

DEFAULT_PROPERTY_NAME: Final = "Home"
DEFAULT_OBSERVER_GUIDANCE: Final = ""
DEFAULT_SUMMARY_TIME: Final = "08:00:00"
DEFAULT_LOOKBACK_HOURS: Final = 24
DEFAULT_RETENTION_DAYS: Final = 45
DEFAULT_LEARNING_ONLY: Final = True
DEFAULT_NOTIFY_DAILY: Final = False
DEFAULT_MIN_SAMPLES: Final = 30
DEFAULT_ZSCORE_THRESHOLD: Final = 3.5
DEFAULT_AUTO_DISCOVERY: Final = True
DEFAULT_DISCOVERY_INTERVAL_HOURS: Final = 168
DEFAULT_EVENT_WINDOW_SECONDS: Final = 300
DEFAULT_MAX_EVENTS: Final = 5000
DEFAULT_MAX_SUMMARIES: Final = 120
DEFAULT_ANOMALY_COOLDOWN_MINUTES: Final = 30
DEFAULT_BASELINE_HALF_LIFE_DAYS: Final = 30.0
FAILURES_BEFORE_REPAIR: Final = 2

STORAGE_VERSION: Final = 2
STORAGE_KEY_PREFIX: Final = f"{DOMAIN}."

SERVICE_GENERATE_SUMMARY: Final = "generate_summary"
SERVICE_RECORD_NOTE: Final = "record_note"
SERVICE_FORGET_NOTE: Final = "forget_note"
SERVICE_SET_STAY_CONTEXT: Final = "set_stay_context"
SERVICE_DISCOVER_ENTITIES: Final = "discover_entities"

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_HOURS: Final = "hours"
ATTR_NOTE: Final = "note"
ATTR_NOTE_ID: Final = "note_id"
ATTR_CATEGORY: Final = "category"
ATTR_EXPIRES_IN_DAYS: Final = "expires_in_days"
ATTR_RESERVATION_ID: Final = "reservation_id"
ATTR_LABEL: Final = "label"
ATTR_GUEST_COUNT: Final = "guest_count"
ATTR_PET_COUNT: Final = "pet_count"
ATTR_CHECK_IN: Final = "check_in"
ATTR_CHECK_OUT: Final = "check_out"
ATTR_CLEAR: Final = "clear"

NOTE_CATEGORIES: Final = (
    "owner",
    "maintenance",
    "guest_report",
    "configuration_change",
    "property_pattern",
)

SEVERITIES: Final = ("normal", "note", "watch", "action")
OCCUPANCY_STATES: Final = ("occupied", "vacant", "unknown")

EVENT_SUMMARY_GENERATED: Final = "house_observer_summary_generated"
EVENT_DISCOVERY_COMPLETED: Final = "house_observer_discovery_completed"

LLM_API_ID: Final = "house_observer"
