"""House log, LLM tools, and the analysis agent inside Home Assistant."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.conversation import ConversationResult
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent, llm
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.house_observer.const import DOMAIN

DOOR = "binary_sensor.sliding_door"
POWER = "sensor.house_power"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Any, enable_custom_integrations: None
) -> None:
    """Start the recorder before Home Assistant for every test here."""


LLM_CONTEXT = llm.LLMContext(
    platform="test", context=None, language=None, assistant=None, device_id=None
)


async def _setup(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test House",
        data={"property_name": "Test House"},
        options={
            "observer_mode": "home",
            "auto_discovery": False,
            "always_monitor_entities": [DOOR, POWER],
            **options,
        },
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _record_activity(hass: HomeAssistant) -> None:
    door = {"device_class": "door", "friendly_name": "Sliding door"}
    power = {"unit_of_measurement": "W", "friendly_name": "House power"}
    hass.states.async_set(DOOR, "off", door)
    hass.states.async_set(POWER, "1200", power)
    for state in ("on", "off", "on", "off"):
        hass.states.async_set(DOOR, state, door)
        await hass.async_block_till_done()
    for value in ("1210", "2600", "2650", "1190"):
        hass.states.async_set(POWER, value, power)
        await hass.async_block_till_done()
    await async_wait_recording_done(hass)


async def test_summary_reads_full_house_log_from_recorder(
    recorder_mock: Any, hass: HomeAssistant, fake_ai: Any
) -> None:
    await _record_activity(hass)
    entry = await _setup(hass)
    summary = await entry.runtime_data.async_generate_summary()
    prompt = fake_ai.last_instructions
    assert "HOUSE LOG" in prompt
    door_line = next(line for line in prompt.splitlines() if line.startswith("Sliding"))
    assert door_line.count("open ") == 2
    assert "2.60k..2.65k" in prompt
    assert '"recent_events"' not in prompt
    assert summary.analysis_path == "ai_task"


async def test_llm_tools_read_monitored_entities_only(
    recorder_mock: Any, hass: HomeAssistant
) -> None:
    await _record_activity(hass)
    hass.states.async_set("sensor.private", "1")
    await _setup(hass)
    api = await llm.async_get_api(hass, "house_observer", LLM_CONTEXT)
    assert {tool.name for tool in api.tools} == {
        "house_observer_list_entities",
        "house_observer_get_log",
        "house_observer_get_readings",
        "house_observer_get_context",
    }

    listed = await api.async_call_tool(
        llm.ToolInput("house_observer_list_entities", {})
    )
    assert {item["entity_id"] for item in listed["entities"]} == {DOOR, POWER}

    readings = await api.async_call_tool(
        llm.ToolInput("house_observer_get_readings", {"entity_id": POWER, "hours": 2})
    )
    assert [value for _, value in readings["readings"]][-4:] == [
        "1210",
        "2600",
        "2650",
        "1190",
    ]
    door = await api.async_call_tool(
        llm.ToolInput("house_observer_get_readings", {"entity_id": DOOR})
    )
    assert [value for _, value in door["readings"]][-2:] == ["open", "closed"]

    log = await api.async_call_tool(
        llm.ToolInput("house_observer_get_log", {"entity_ids": [DOOR]})
    )
    assert "Sliding door" in log["log"] and "House power" not in log["log"]

    with pytest.raises(HomeAssistantError):
        await api.async_call_tool(
            llm.ToolInput(
                "house_observer_get_readings", {"entity_id": "sensor.private"}
            )
        )


def _reply(text: str) -> ConversationResult:
    response = intent.IntentResponse(language="en")
    response.async_set_speech(text)
    return ConversationResult(response=response)


async def test_analysis_agent_is_used_and_parsed(
    hass: HomeAssistant, fake_ai: Any
) -> None:
    entry = await _setup(hass, analysis_agent="conversation.analyst")
    reply = _reply(
        'Here you go:\n```json\n{"summary": "Peak was clean.", "severity": '
        '"note", "confidence": 0.8, "notify_owner": false}\n```'
    )
    with patch(
        "homeassistant.components.conversation.async_converse", return_value=reply
    ) as converse:
        summary = await entry.runtime_data.async_generate_summary()
    prompt = converse.call_args.kwargs["text"]
    assert "house_observer tools" in prompt
    assert "Never control" in prompt
    assert converse.call_args.kwargs["agent_id"] == "conversation.analyst"
    assert summary.summary == "Peak was clean."
    assert summary.analysis_path == "agent"
    assert fake_ai.calls == []


async def test_analysis_agent_failure_falls_back_to_ai_task(
    hass: HomeAssistant, fake_ai: Any
) -> None:
    entry = await _setup(hass, analysis_agent="conversation.analyst")
    with patch(
        "homeassistant.components.conversation.async_converse",
        return_value=_reply("Sorry, I can't help with that."),
    ):
        summary = await entry.runtime_data.async_generate_summary()
    assert summary.analysis_path == "ai_task"
    assert summary.ai_generated
    status = hass.states.get("sensor.test_house_status")
    assert status.state != "error"
    assert "summary JSON" in status.attributes["analysis_agent_error"]
