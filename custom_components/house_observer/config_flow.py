"""Config flow for House Observer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    DeviceSelector,
    DeviceSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TimeSelector,
)
from homeassistant.util import slugify

from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_ALWAYS_MONITOR_DEVICES,
    CONF_ALWAYS_MONITOR_ENTITIES,
    CONF_AUTO_DISCOVERY,
    CONF_DISCOVERY_INTERVAL_HOURS,
    CONF_LEARNING_ONLY,
    CONF_LOOKBACK_HOURS,
    CONF_MIN_SAMPLES,
    CONF_MODE,
    CONF_NEVER_MONITOR_DEVICES,
    CONF_NEVER_MONITOR_ENTITIES,
    CONF_NOTIFY_DAILY,
    CONF_NOTIFY_SERVICE,
    CONF_OBSERVER_GUIDANCE,
    CONF_OCCUPANCY_ENTITY,
    CONF_PROPERTY_NAME,
    CONF_RESERVATION_ENTITIES,
    CONF_RETENTION_DAYS,
    CONF_SUMMARY_TIME,
    CONF_ZSCORE_THRESHOLD,
    DEFAULT_AUTO_DISCOVERY,
    DEFAULT_DISCOVERY_INTERVAL_HOURS,
    DEFAULT_LEARNING_ONLY,
    DEFAULT_LOOKBACK_HOURS,
    DEFAULT_MIN_SAMPLES,
    DEFAULT_MODE,
    DEFAULT_NOTIFY_DAILY,
    DEFAULT_OBSERVER_GUIDANCE,
    DEFAULT_PROPERTY_NAME,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_SUMMARY_TIME,
    DEFAULT_ZSCORE_THRESHOLD,
    DOMAIN,
    MODE_RENTAL,
    MODES,
)

OCCUPANCY_DOMAINS = ["input_boolean", "binary_sensor", "sensor", "zone", "group"]


def _box(minimum: float, maximum: float, step: float = 1) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX
        )
    )


def _mode_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=list(MODES),
            translation_key="observer_mode",
            mode=SelectSelectorMode.LIST,
        )
    )


def monitoring_schema(mode: str) -> vol.Schema:
    """What to watch: discovery, pins, ignores, and occupancy sources."""
    fields: dict[Any, Any] = {
        vol.Required(CONF_AUTO_DISCOVERY, default=DEFAULT_AUTO_DISCOVERY): (
            BooleanSelector()
        ),
        vol.Required(
            CONF_DISCOVERY_INTERVAL_HOURS, default=DEFAULT_DISCOVERY_INTERVAL_HOURS
        ): _box(24, 720, 24),
        vol.Optional(CONF_ALWAYS_MONITOR_DEVICES): DeviceSelector(
            DeviceSelectorConfig(multiple=True)
        ),
        vol.Optional(CONF_ALWAYS_MONITOR_ENTITIES): EntitySelector(
            EntitySelectorConfig(multiple=True)
        ),
        vol.Optional(CONF_NEVER_MONITOR_DEVICES): DeviceSelector(
            DeviceSelectorConfig(multiple=True)
        ),
        vol.Optional(CONF_NEVER_MONITOR_ENTITIES): EntitySelector(
            EntitySelectorConfig(multiple=True)
        ),
        vol.Optional(CONF_OCCUPANCY_ENTITY): EntitySelector(
            EntitySelectorConfig(domain=OCCUPANCY_DOMAINS, multiple=False)
        ),
    }
    if mode == MODE_RENTAL:
        fields[vol.Optional(CONF_RESERVATION_ENTITIES)] = EntitySelector(
            EntitySelectorConfig(domain=["calendar", "sensor"], multiple=True)
        )
    return vol.Schema(fields)


def analysis_schema(*, include_mode: bool) -> vol.Schema:
    """How summaries are produced."""
    fields: dict[Any, Any] = {}
    if include_mode:
        fields[vol.Required(CONF_MODE, default=DEFAULT_MODE)] = _mode_selector()
    fields.update(
        {
            vol.Optional(CONF_AI_TASK_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="ai_task", multiple=False)
            ),
            vol.Optional(
                CONF_OBSERVER_GUIDANCE, default=DEFAULT_OBSERVER_GUIDANCE
            ): TextSelector(TextSelectorConfig(multiline=True)),
            vol.Required(CONF_SUMMARY_TIME, default=DEFAULT_SUMMARY_TIME): (
                TimeSelector()
            ),
            vol.Required(CONF_LOOKBACK_HOURS, default=DEFAULT_LOOKBACK_HOURS): _box(
                1, 168
            ),
        }
    )
    return vol.Schema(fields)


def alerts_schema() -> vol.Schema:
    """When the owner is told."""
    return vol.Schema(
        {
            vol.Required(CONF_LEARNING_ONLY, default=DEFAULT_LEARNING_ONLY): (
                BooleanSelector()
            ),
            vol.Optional(CONF_NOTIFY_SERVICE): TextSelector(TextSelectorConfig()),
            vol.Required(CONF_NOTIFY_DAILY, default=DEFAULT_NOTIFY_DAILY): (
                BooleanSelector()
            ),
        }
    )


def learning_schema() -> vol.Schema:
    """Baseline sensitivity and memory retention."""
    return vol.Schema(
        {
            vol.Required(CONF_MIN_SAMPLES, default=DEFAULT_MIN_SAMPLES): _box(10, 1000),
            vol.Required(CONF_ZSCORE_THRESHOLD, default=DEFAULT_ZSCORE_THRESHOLD): (
                _box(2, 10, 0.1)
            ),
            vol.Required(CONF_RETENTION_DAYS, default=DEFAULT_RETENTION_DAYS): _box(
                7, 365
            ),
        }
    )


def _keys(schema: vol.Schema) -> set[str]:
    return {str(key) for key in schema.schema}


class HouseObserverConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a House Observer config flow."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize temporary flow data."""
        self._property_name = DEFAULT_PROPERTY_NAME
        self._options: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the property name and kind."""
        if user_input is not None:
            self._property_name = str(user_input[CONF_PROPERTY_NAME]).strip()
            self._options[CONF_MODE] = user_input[CONF_MODE]
            await self.async_set_unique_id(slugify(self._property_name))
            self._abort_if_unique_id_configured()
            return await self.async_step_monitoring()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PROPERTY_NAME, default=DEFAULT_PROPERTY_NAME
                    ): TextSelector(TextSelectorConfig()),
                    vol.Required(CONF_MODE, default=DEFAULT_MODE): _mode_selector(),
                }
            ),
        )

    async def async_step_monitoring(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect what to watch."""
        if user_input is not None:
            self._options.update(user_input)
            return await self.async_step_analysis()
        return self.async_show_form(
            step_id="monitoring",
            data_schema=monitoring_schema(self._options[CONF_MODE]),
        )

    async def async_step_analysis(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect how summaries are produced."""
        if user_input is not None:
            self._options.update(user_input)
            return await self.async_step_alerts()
        return self.async_show_form(
            step_id="analysis", data_schema=analysis_schema(include_mode=False)
        )

    async def async_step_alerts(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect alerting behavior and create the entry."""
        if user_input is not None:
            self._options.update(user_input)
            return self.async_create_entry(
                title=self._property_name,
                data={CONF_PROPERTY_NAME: self._property_name},
                options=self._options,
            )
        return self.async_show_form(step_id="alerts", data_schema=alerts_schema())

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return HouseObserverOptionsFlow()


class HouseObserverOptionsFlow(config_entries.OptionsFlowWithReload):
    """Edit one settings section at a time and reload automatically."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the settings sections."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["monitoring", "analysis", "alerts", "learning"],
        )

    @property
    def _mode(self) -> str:
        return str(self.config_entry.options.get(CONF_MODE, DEFAULT_MODE))

    def _section(
        self, step_id: str, schema: vol.Schema, user_input: Mapping[str, Any] | None
    ) -> ConfigFlowResult:
        if user_input is not None:
            # Cleared optional fields are absent from user_input, so drop this
            # section's previous values before merging rather than keeping them.
            keys = _keys(schema)
            options = {
                key: value
                for key, value in self.config_entry.options.items()
                if key not in keys
            }
            options.update(user_input)
            return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                schema, self.config_entry.options
            ),
        )

    async def async_step_monitoring(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit what is watched."""
        return self._section("monitoring", monitoring_schema(self._mode), user_input)

    async def async_step_analysis(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit how summaries are produced."""
        return self._section("analysis", analysis_schema(include_mode=True), user_input)

    async def async_step_alerts(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit when the owner is told."""
        return self._section("alerts", alerts_schema(), user_input)

    async def async_step_learning(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit baseline sensitivity and retention."""
        return self._section("learning", learning_schema(), user_input)
