"""Thin wrapper around Home Assistant's AI Task action."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


class AITaskUnavailable(Exception):
    """Raised when no AI Task provider is installed."""


class AITaskBadResponse(Exception):
    """Raised when AI Task returns something other than structured data."""


def describe_error(err: BaseException) -> str:
    """Return a short, log-free description suitable for a sensor attribute."""
    message = str(err).strip() or "no details"
    text = f"{type(err).__name__}: {message}"
    return text if len(text) <= 300 else f"{text[:299]}…"


async def async_generate_data(
    hass: HomeAssistant,
    *,
    task_name: str,
    instructions: str,
    structure: dict[str, Any],
    entity_id: str | None,
) -> dict[str, Any]:
    """Call ai_task.generate_data and return its structured data."""
    if not hass.services.has_service("ai_task", "generate_data"):
        raise AITaskUnavailable("The ai_task.generate_data action is unavailable")
    service_data: dict[str, Any] = {
        "task_name": task_name,
        "instructions": instructions,
        "structure": structure,
    }
    if entity_id:
        service_data["entity_id"] = entity_id
    response = await hass.services.async_call(
        "ai_task",
        "generate_data",
        service_data,
        blocking=True,
        return_response=True,
    )
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        raise AITaskBadResponse(
            f"Expected structured data, received {type(data).__name__}"
        )
    return data
