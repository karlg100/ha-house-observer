"""End-to-end tests inside a real Home Assistant instance."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.house_observer.const import DOMAIN


async def _setup(
    hass: HomeAssistant,
    options: dict,
    *,
    data: dict | None = None,
    minor_version: int = 2,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test House",
        data={"property_name": "Test House", **(data or {})},
        options=options,
        version=1,
        minor_version=minor_version,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow_creates_entry_with_mode(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"property_name": "Lake House", "observer_mode": "rental"}
    )
    assert result["step_id"] == "monitoring"
    schema_keys = {str(key) for key in result["data_schema"].schema}
    assert "reservation_entities" in schema_keys
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"auto_discovery": False, "discovery_interval_hours": 168}
    )
    assert result["step_id"] == "analysis"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"summary_time": "08:00:00", "lookback_hours": 24}
    )
    assert result["step_id"] == "alerts"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"learning_only": True, "notify_daily_summary": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["observer_mode"] == "rental"


async def test_options_menu_clears_removed_fields(hass: HomeAssistant) -> None:
    entry = await _setup(
        hass,
        {
            "observer_mode": "home",
            "auto_discovery": False,
            "always_monitor_entities": ["sensor.a"],
            "notify_service": "notify.phone",
        },
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "monitoring"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"auto_discovery": False, "discovery_interval_hours": 168}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert "always_monitor_entities" not in entry.options
    # Other sections are untouched.
    assert entry.options["notify_service"] == "notify.phone"


async def test_legacy_entry_migrates_pickers_and_mode(hass: HomeAssistant) -> None:
    entry = await _setup(
        hass,
        {
            "auto_discovery": False,
            "spa_entities": ["sensor.spa_temp"],
            "access_entities": ["binary_sensor.door"],
            "reservation_entities": ["calendar.bookings"],
        },
        minor_version=1,
    )
    assert entry.minor_version == 2
    assert entry.options["observer_mode"] == "rental"
    assert "spa_entities" not in entry.options
    assert set(entry.options["always_monitor_entities"]) == {
        "sensor.spa_temp",
        "binary_sensor.door",
    }
    assert entry.data["category_overrides"] == {
        "sensor.spa_temp": "spa",
        "binary_sensor.door": "access",
    }
    observer = entry.runtime_data
    assert observer.category_for("sensor.spa_temp") == "spa"


async def test_storage_migration_moves_notes_into_knowledge(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    now = dt_util.now().isoformat()
    hass_storage["house_observer.legacy"] = {
        "version": 1,
        "minor_version": 1,
        "key": "house_observer.legacy",
        "data": {
            "events": [
                {
                    "timestamp": now,
                    "entity_id": "house_observer.note",
                    "name": "House note",
                    "category": "maintenance",
                    "old_state": None,
                    "new_state": "Replaced the spa heater",
                }
            ],
            "patterns": {
                "entities": {
                    "sensor.t": {
                        "numeric": {"count": 3, "mean": 1, "m2": 0},
                        "state_counts": {"1.0": 1, "2.0": 1, "3.0": 1},
                        "hour_counts": [0] * 24,
                        "observations": 3,
                    }
                }
            },
        },
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="legacy",
        data={"property_name": "Legacy"},
        options={"auto_discovery": False},
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    observer = entry.runtime_data
    assert [item.text for item in observer.knowledge] == ["Replaced the spa heater"]
    assert observer.events == []
    assert observer.patterns.entities["sensor.t"]["state_counts"] == {}


async def test_rental_summary_is_mode_aware_and_private(
    hass: HomeAssistant, fake_ai: Any
) -> None:
    hass.states.async_set(
        "binary_sensor.hot_tub_cover", "on", {"device_class": "opening"}
    )
    entry = await _setup(
        hass,
        {
            "observer_mode": "rental",
            "auto_discovery": False,
            "always_monitor_entities": ["binary_sensor.hot_tub_cover"],
        },
    )
    observer = entry.runtime_data
    await hass.services.async_call(
        DOMAIN,
        "set_stay_context",
        {
            "reservation_id": "HMSECRET1",
            "label": "Smith family",
            "guest_count": 4,
            "check_in": (dt_util.now() - timedelta(hours=3)).isoformat(),
            "check_out": (dt_util.now() + timedelta(days=2)).isoformat(),
        },
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        "record_note",
        {"note": "Hot tub cover latch is stiff", "category": "maintenance"},
        blocking=True,
    )
    assert hass.states.get("sensor.test_house_occupancy").state == "occupied"

    summary = await observer.async_generate_summary(reason="manual", hours=24)
    prompt = fake_ai.last_instructions
    assert "short-term rental" in prompt
    assert "Guests are staying" in prompt
    assert "Hot tub cover latch is stiff" in prompt
    assert '"guest_count":4' in prompt
    assert "HMSECRET1" not in prompt
    assert "Smith" not in prompt
    assert '"for_minutes"' in prompt
    assert summary.occupancy == "occupied"
    assert hass.states.get("sensor.test_house_condition").state == "normal"
    assert hass.states.get("sensor.test_house_property_notes").state == "1"


async def test_summary_failure_surfaces_error_and_repair(
    hass: HomeAssistant, fake_ai: Any
) -> None:
    entry = await _setup(hass, {"observer_mode": "home", "auto_discovery": False})
    observer = entry.runtime_data
    fake_ai.error = RuntimeError("provider timed out")

    await observer.async_generate_summary()
    await hass.async_block_till_done()
    status = hass.states.get("sensor.test_house_status")
    assert status.state == "error"
    assert "provider timed out" in status.attributes["last_error"]
    issue_id = f"summary_failed_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    summary = await observer.async_generate_summary()
    assert not summary.ai_generated
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    fake_ai.error = None
    await observer.async_generate_summary()
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert hass.states.get("sensor.test_house_status").state == "learning"


async def test_discovery_failure_is_recorded_not_retried_hourly(
    hass: HomeAssistant, fake_ai: Any
) -> None:
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", "test", "flow", suggested_object_id="water_flow"
    )
    hass.states.async_set("sensor.water_flow", "0", {"unit_of_measurement": "L/min"})
    fake_ai.error = ValueError("schema rejected")
    entry = await _setup(hass, {"observer_mode": "home", "auto_discovery": True})
    observer = entry.runtime_data

    response = await observer.discovery.async_discover(reason="manual")
    assert response["last_error"] == "ValueError: schema rejected"
    assert response["consecutive_failures"] == 1
    assert response["schedule_phase"] == "daily_retry"
    assert not observer.discovery.is_due(dt_util.now() + timedelta(hours=2))

    fake_ai.error = None
    fake_ai.recommendations = "sensor.water_flow | water | Detects leaks"
    response = await observer.discovery.async_discover(reason="manual")
    assert response["last_error"] is None
    assert observer.tracked_entities == ["sensor.water_flow"]
    assert observer.category_for("sensor.water_flow") == "water"


async def test_labels_pin_ignore_and_categorize(hass: HomeAssistant) -> None:
    labels = lr.async_get(hass)
    water = labels.async_create("Observer water")
    ignore = labels.async_create("Observer ignore")
    registry = er.async_get(hass)
    meter = registry.async_get_or_create(
        "sensor", "test", "meter", suggested_object_id="meter"
    )
    noisy = registry.async_get_or_create(
        "sensor", "test", "noisy", suggested_object_id="noisy"
    )
    registry.async_update_entity(meter.entity_id, labels={water.label_id})
    registry.async_update_entity(noisy.entity_id, labels={ignore.label_id})
    hass.states.async_set("sensor.meter", "1")
    hass.states.async_set("sensor.noisy", "1")

    entry = await _setup(
        hass,
        {
            "observer_mode": "home",
            "auto_discovery": False,
            "always_monitor_entities": ["sensor.noisy"],
        },
    )
    observer = entry.runtime_data
    assert observer.tracked_entities == ["sensor.meter"]
    assert observer.category_for("sensor.meter") == "water"


async def test_numeric_spike_survives_throttle_and_stay_expires(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set("sensor.power", "100")
    entry = await _setup(
        hass,
        {
            "observer_mode": "rental",
            "auto_discovery": False,
            "always_monitor_entities": ["sensor.power"],
        },
    )
    observer = entry.runtime_data
    for value in ("200", "5000", "210"):
        hass.states.async_set("sensor.power", value)
        await hass.async_block_till_done()
    # One stored event; the spike is still visible in later window statistics.
    assert len(observer.events) == 1
    assert observer.patterns.entities["sensor.power"]["numeric"].maximum == 5000

    observer.stay_context = {
        "check_in": (dt_util.now() - timedelta(days=2)).isoformat(),
        "check_out": (dt_util.now() - timedelta(minutes=5)).isoformat(),
    }
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=1, minutes=1))
    await hass.async_block_till_done()
    assert observer.stay_context == {}


async def test_registry_churn_does_not_trigger_hourly_discovery(
    hass: HomeAssistant, fake_ai: Any
) -> None:
    fake_ai.recommendations = ""
    entry = await _setup(hass, {"observer_mode": "home", "auto_discovery": True})
    observer = entry.runtime_data
    await observer.discovery.async_discover(reason="manual")
    calls = len(fake_ai.calls)

    registry = er.async_get(hass)
    for index in range(5):
        registry.async_get_or_create(
            "device_tracker", "unifi", f"client{index}", suggested_object_id=f"c{index}"
        )
    await hass.async_block_till_done()
    assert not observer.discovery.state.get("registry_change_pending")

    registry.async_get_or_create(
        "sensor", "test", "new_meter", suggested_object_id="new_meter"
    )
    await hass.async_block_till_done()
    assert observer.discovery.state["registry_change_pending"]

    # The last review was moments ago, so the daily limit holds it back.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=2))
    await hass.async_block_till_done()
    assert len(fake_ai.calls) == calls


async def test_rental_summary_reports_guest_estimate(
    hass: HomeAssistant, fake_ai: Any
) -> None:
    hass.states.async_set(
        "sensor.booking",
        "Booking",
        {
            "start": (dt_util.now() - timedelta(hours=3)).isoformat(),
            "end": (dt_util.now() + timedelta(days=2)).isoformat(),
            "party": "4 adults and 2 children",
            "name": "Jane Smith",
        },
    )
    entry = await _setup(
        hass,
        {
            "observer_mode": "rental",
            "auto_discovery": False,
            "reservation_entities": ["sensor.booking"],
        },
    )
    fake_ai.summary["occupancy_estimate"] = "About 5-7 people (3 areas busy at once)."
    summary = await entry.runtime_data.async_generate_summary()
    prompt = fake_ai.last_instructions
    assert '"guest_count":6' in prompt
    assert "Jane" not in prompt
    assert "occupancy_estimate" in prompt
    assert summary.occupancy_estimate.startswith("About 5-7 people")
    attributes = hass.states.get("sensor.test_house_last_summary").attributes
    assert attributes["occupancy_estimate"].startswith("About 5-7")


async def test_home_summary_never_stores_a_guest_estimate(
    hass: HomeAssistant, fake_ai: Any
) -> None:
    entry = await _setup(hass, {"observer_mode": "home", "auto_discovery": False})
    fake_ai.summary["occupancy_estimate"] = "3 people"
    summary = await entry.runtime_data.async_generate_summary()
    assert summary.occupancy_estimate == ""
    assert "estimate how many people" not in fake_ai.last_instructions
