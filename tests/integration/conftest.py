"""Fixtures for tests that run the integration inside Home Assistant."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse  # noqa: E402


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load custom_components/house_observer."""


class FakeAITask:
    """A stand-in ai_task.generate_data action that records its calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.summary: dict[str, Any] = {
            "summary": "All quiet.",
            "severity": "normal",
            "confidence": 0.9,
            "notify_owner": False,
        }
        self.recommendations: str = ""
        self.error: Exception | None = None
        self.error_once: Exception | None = None

    async def handle(self, call: ServiceCall) -> dict[str, Any]:
        self.calls.append(dict(call.data))
        if self.error is not None:
            raise self.error
        if self.error_once is not None:
            error, self.error_once = self.error_once, None
            raise error
        if "recommendations" in call.data["structure"]:
            return {
                "data": {"recommendations": self.recommendations, "confidence": 0.8}
            }
        return {"data": dict(self.summary)}

    @property
    def last_instructions(self) -> str:
        return str(self.calls[-1]["instructions"])


@pytest.fixture
def fake_ai(hass: HomeAssistant) -> FakeAITask:
    """Register a fake AI Task provider."""
    fake = FakeAITask()
    hass.services.async_register(
        "ai_task", "generate_data", fake.handle, supports_response=SupportsResponse.ONLY
    )
    return fake
