"""House Observer integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_CATEGORY,
    ATTR_CHECK_IN,
    ATTR_CHECK_OUT,
    ATTR_CLEAR,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_EXPIRES_IN_DAYS,
    ATTR_GUEST_COUNT,
    ATTR_HOURS,
    ATTR_LABEL,
    ATTR_NOTE,
    ATTR_NOTE_ID,
    ATTR_PET_COUNT,
    ATTR_RESERVATION_ID,
    CONF_ALWAYS_MONITOR_ENTITIES,
    CONF_MODE,
    CONF_RESERVATION_ENTITIES,
    DATA_CATEGORY_OVERRIDES,
    DEFAULT_LOOKBACK_HOURS,
    DOMAIN,
    LEGACY_GROUP_CATEGORIES,
    MODE_HOME,
    MODE_RENTAL,
    NOTE_CATEGORIES,
    SERVICE_DISCOVER_ENTITIES,
    SERVICE_FORGET_NOTE,
    SERVICE_GENERATE_SUMMARY,
    SERVICE_RECORD_NOTE,
    SERVICE_SET_STAY_CONTEXT,
)
from .observer import HouseObserver
from .repairs import async_clear_all

PLATFORMS = [Platform.SENSOR]

type HouseObserverConfigEntry = ConfigEntry[HouseObserver]

DATA_MANAGERS = "managers"
DATA_SERVICES_REGISTERED = "services_registered"


def _manager_for_call(hass: HomeAssistant, call: ServiceCall) -> HouseObserver:
    """Resolve a target manager, requiring an ID when several exist."""
    managers: dict[str, HouseObserver] = hass.data[DOMAIN][DATA_MANAGERS]
    if entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID):
        if manager := managers.get(entry_id):
            return manager
        raise ServiceValidationError(
            f"No loaded House Observer config entry named {entry_id}"
        )
    if len(managers) == 1:
        return next(iter(managers.values()))
    if not managers:
        raise ServiceValidationError("No House Observer config entry is loaded")
    raise ServiceValidationError(
        "config_entry_id is required when more than one property is configured"
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-level actions."""
    domain_data = hass.data.setdefault(
        DOMAIN, {DATA_MANAGERS: {}, DATA_SERVICES_REGISTERED: False}
    )
    if domain_data[DATA_SERVICES_REGISTERED]:
        return True

    async def generate_summary(call: ServiceCall) -> ServiceResponse:
        manager = _manager_for_call(hass, call)
        summary = await manager.async_generate_summary(
            reason="manual",
            hours=int(call.data.get(ATTR_HOURS, DEFAULT_LOOKBACK_HOURS)),
        )
        return summary.as_dict()

    async def record_note(call: ServiceCall) -> ServiceResponse:
        manager = _manager_for_call(hass, call)
        item = await manager.async_record_note(
            str(call.data[ATTR_NOTE]),
            str(call.data.get(ATTR_CATEGORY, "owner")),
            expires_in_days=call.data.get(ATTR_EXPIRES_IN_DAYS),
        )
        return item.as_dict()

    async def forget_note(call: ServiceCall) -> ServiceResponse:
        manager = _manager_for_call(hass, call)
        note_id = str(call.data[ATTR_NOTE_ID])
        if not await manager.async_forget_note(note_id):
            raise ServiceValidationError(f"No House Observer note with ID {note_id}")
        return {"note_id": note_id, "removed": True}

    async def discover_entities(call: ServiceCall) -> ServiceResponse:
        manager = _manager_for_call(hass, call)
        return await manager.discovery.async_discover(reason="manual")

    async def set_stay_context(call: ServiceCall) -> None:
        manager = _manager_for_call(hass, call)
        if call.data.get(ATTR_CLEAR, False):
            await manager.async_set_stay_context({})
            return
        allowed = (
            ATTR_RESERVATION_ID,
            ATTR_LABEL,
            ATTR_GUEST_COUNT,
            ATTR_PET_COUNT,
            ATTR_CHECK_IN,
            ATTR_CHECK_OUT,
        )
        context: dict[str, Any] = {
            key: value
            for key in allowed
            if (value := call.data.get(key)) not in (None, "")
        }
        await manager.async_set_stay_context(context)

    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE_SUMMARY,
        generate_summary,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
                vol.Optional(ATTR_HOURS, default=DEFAULT_LOOKBACK_HOURS): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=168)
                ),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISCOVER_ENTITIES,
        discover_entities,
        schema=vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RECORD_NOTE,
        record_note,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
                vol.Required(ATTR_NOTE): cv.string,
                vol.Optional(ATTR_CATEGORY, default="owner"): vol.In(NOTE_CATEGORIES),
                vol.Optional(ATTR_EXPIRES_IN_DAYS): vol.All(
                    vol.Coerce(float), vol.Range(min=0.1, max=3650)
                ),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_FORGET_NOTE,
        forget_note,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
                vol.Required(ATTR_NOTE_ID): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_STAY_CONTEXT,
        set_stay_context,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
                vol.Optional(ATTR_CLEAR, default=False): cv.boolean,
                vol.Optional(ATTR_RESERVATION_ID): cv.string,
                vol.Optional(ATTR_LABEL): cv.string,
                vol.Optional(ATTR_GUEST_COUNT): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=100)
                ),
                vol.Optional(ATTR_PET_COUNT): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=100)
                ),
                vol.Optional(ATTR_CHECK_IN): cv.string,
                vol.Optional(ATTR_CHECK_OUT): cv.string,
            }
        ),
    )
    domain_data[DATA_SERVICES_REGISTERED] = True
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: HouseObserverConfigEntry
) -> bool:
    """Set up one observed property."""
    manager = HouseObserver(hass, entry)
    entry.runtime_data = manager
    hass.data[DOMAIN][DATA_MANAGERS][entry.entry_id] = manager
    await manager.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: HouseObserverConfigEntry
) -> bool:
    """Unload one observed property."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
        hass.data[DOMAIN][DATA_MANAGERS].pop(entry.entry_id, None)
    return unloaded


async def async_remove_entry(
    hass: HomeAssistant, entry: HouseObserverConfigEntry
) -> None:
    """Remove Repairs issues left by a deleted property."""
    async_clear_all(hass, entry.entry_id)


def migrate_options(
    options: dict[str, Any], overrides: dict[str, str]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Fold 0.2 category pickers into pins plus category overrides."""
    options = dict(options)
    overrides = dict(overrides)
    pins = list(options.get(CONF_ALWAYS_MONITOR_ENTITIES, []))
    for key, category in LEGACY_GROUP_CATEGORIES.items():
        for entity_id in options.pop(key, None) or []:
            overrides.setdefault(entity_id, category)
            if entity_id not in pins:
                pins.append(entity_id)
    if pins:
        options[CONF_ALWAYS_MONITOR_ENTITIES] = pins
    # Earlier releases assumed a rental. Keep that for anyone who wired up
    # booking data; everyone else becomes a home observer.
    options.setdefault(
        CONF_MODE,
        MODE_RENTAL if options.get(CONF_RESERVATION_ENTITIES) else MODE_HOME,
    )
    return options, overrides


async def async_migrate_entry(
    hass: HomeAssistant, entry: HouseObserverConfigEntry
) -> bool:
    """Migrate config entries across schema versions."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        options, overrides = migrate_options(
            dict(entry.options), dict(entry.data.get(DATA_CATEGORY_OVERRIDES, {}))
        )
        data = dict(entry.data)
        if overrides:
            data[DATA_CATEGORY_OVERRIDES] = overrides
        hass.config_entries.async_update_entry(
            entry, data=data, options=options, minor_version=2
        )
    return True
