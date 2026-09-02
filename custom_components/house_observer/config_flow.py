"""Config flow for House Observer."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TimeSelector,
)
from homeassistant.util import slugify

from .const import (
    CONF_ACCESS_ENTITIES,
    CONF_ACTIVITY_ENTITIES,
    CONF_AI_TASK_ENTITY,
    CONF_ENERGY_ENTITIES,
    CONF_HVAC_ENTITIES,
    CONF_LEARNING_ONLY,
    CONF_LOOKBACK_HOURS,
    CONF_MIN_SAMPLES,
    CONF_NETWORK_ENTITIES,
    CONF_NOTIFY_DAILY,
    CONF_NOTIFY_SERVICE,
    CONF_OCCUPANCY_ENTITIES,
    CONF_PROPERTY_NAME,
    CONF_RESERVATION_ENTITIES,
    CONF_RETENTION_DAYS,
    CONF_SPA_ENTITIES,
    CONF_SUMMARY_TIME,
    CONF_ZSCORE_THRESHOLD,
    DEFAULT_LEARNING_ONLY,
    DEFAULT_LOOKBACK_HOURS,
    DEFAULT_MIN_SAMPLES,
    DEFAULT_NOTIFY_DAILY,
    DEFAULT_PROPERTY_NAME,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_SUMMARY_TIME,
    DEFAULT_ZSCORE_THRESHOLD,
    DOMAIN,
)


def _entity_selector(*, domain: str | None = None) -> EntitySelector:
    """Create a multiple-entity selector."""
    return EntitySelector(EntitySelectorConfig(domain=domain, multiple=True))


def _entities_schema() -> vol.Schema:
    """Return the semantic entity grouping schema."""
    return vol.Schema(
        {
            vol.Optional(CONF_ACTIVITY_ENTITIES): _entity_selector(),
            vol.Optional(CONF_ACCESS_ENTITIES): _entity_selector(),
            vol.Optional(CONF_OCCUPANCY_ENTITIES): _entity_selector(),
            vol.Optional(CONF_SPA_ENTITIES): _entity_selector(),
            vol.Optional(CONF_HVAC_ENTITIES): _entity_selector(),
            vol.Optional(CONF_NETWORK_ENTITIES): _entity_selector(),
            vol.Optional(CONF_ENERGY_ENTITIES): _entity_selector(),
            vol.Optional(CONF_RESERVATION_ENTITIES): _entity_selector(),
        }
    )


def _behavior_schema() -> vol.Schema:
    """Return observer behavior settings."""
    return vol.Schema(
        {
            vol.Optional(CONF_AI_TASK_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="ai_task", multiple=False)
            ),
            vol.Required(
                CONF_SUMMARY_TIME, default=DEFAULT_SUMMARY_TIME
            ): TimeSelector(),
            vol.Required(
                CONF_LOOKBACK_HOURS, default=DEFAULT_LOOKBACK_HOURS
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=168, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_RETENTION_DAYS, default=DEFAULT_RETENTION_DAYS
            ): NumberSelector(
                NumberSelectorConfig(
                    min=7, max=365, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Required(CONF_MIN_SAMPLES, default=DEFAULT_MIN_SAMPLES): NumberSelector(
                NumberSelectorConfig(
                    min=10, max=1000, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_ZSCORE_THRESHOLD, default=DEFAULT_ZSCORE_THRESHOLD
            ): NumberSelector(
                NumberSelectorConfig(
                    min=2, max=10, step=0.1, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_LEARNING_ONLY, default=DEFAULT_LEARNING_ONLY
            ): BooleanSelector(),
            vol.Optional(CONF_NOTIFY_SERVICE): TextSelector(TextSelectorConfig()),
            vol.Required(
                CONF_NOTIFY_DAILY, default=DEFAULT_NOTIFY_DAILY
            ): BooleanSelector(),
        }
    )


def _all_options_schema() -> vol.Schema:
    """Return one schema for editing an existing entry."""
    combined: dict[Any, Any] = {}
    combined.update(_entities_schema().schema)
    combined.update(_behavior_schema().schema)
    return vol.Schema(combined)


class HouseObserverConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a House Observer config flow."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize temporary flow data."""
        self._property_name = DEFAULT_PROPERTY_NAME
        self._options: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the property identity."""
        if user_input is not None:
            self._property_name = str(user_input[CONF_PROPERTY_NAME]).strip()
            await self.async_set_unique_id(slugify(self._property_name))
            self._abort_if_unique_id_configured()
            return await self.async_step_entities()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PROPERTY_NAME, default=DEFAULT_PROPERTY_NAME
                    ): TextSelector(TextSelectorConfig())
                }
            ),
        )

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect entities grouped by meaning."""
        if user_input is not None:
            self._options.update(user_input)
            return await self.async_step_behavior()
        return self.async_show_form(step_id="entities", data_schema=_entities_schema())

    async def async_step_behavior(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect summary, learning, and notification behavior."""
        if user_input is not None:
            self._options.update(user_input)
            return self.async_create_entry(
                title=self._property_name,
                data={CONF_PROPERTY_NAME: self._property_name},
                options=self._options,
            )
        return self.async_show_form(step_id="behavior", data_schema=_behavior_schema())

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return HouseObserverOptionsFlow()


class HouseObserverOptionsFlow(config_entries.OptionsFlowWithReload):
    """Edit House Observer settings and reload automatically."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit all mutable settings."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                _all_options_schema(), self.config_entry.options
            ),
        )
