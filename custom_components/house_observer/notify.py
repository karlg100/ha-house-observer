"""Notification policy and delivery."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .models import ObserverSummary

_LOGGER = logging.getLogger(__name__)


def should_notify(
    summary: ObserverSummary, *, learning_only: bool, notify_daily: bool
) -> bool:
    """Apply deterministic rules so a model response alone cannot alert.

    Daily summaries follow their own switch. Everything else needs learning
    mode off, a WATCH or ACTION severity, and an explicit notify_owner flag.
    """
    if summary.reason == "daily":
        return notify_daily
    return (
        not learning_only
        and summary.notify_owner
        and summary.severity in {"watch", "action"}
    )


async def async_send(
    hass: HomeAssistant, target: str, *, title: str, message: str
) -> bool:
    """Send through a notify entity or a legacy notify action."""
    target = target.strip()
    if "." not in target:
        return False
    if target.startswith("notify.") and hass.states.get(target) is not None:
        await hass.services.async_call(
            "notify",
            "send_message",
            {"entity_id": target, "title": title, "message": message},
            blocking=False,
        )
        return True
    domain, service = target.split(".", 1)
    if not hass.services.has_service(domain, service):
        _LOGGER.warning("Notification target %s does not exist", target)
        return False
    await hass.services.async_call(
        domain, service, {"title": title, "message": message}, blocking=False
    )
    return True
