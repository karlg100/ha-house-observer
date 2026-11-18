"""Repairs issues for persistent House Observer failures."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, FAILURES_BEFORE_REPAIR

ISSUE_KINDS = ("discovery_failed", "summary_failed", "output_limit_low")


def _issue_id(kind: str, entry_id: str) -> str:
    return f"{kind}_{entry_id}"


def async_report_failure(
    hass: HomeAssistant,
    *,
    kind: str,
    entry_id: str,
    property_name: str,
    failures: int,
    error: str,
) -> None:
    """Raise a Repairs issue once failures are persistent, not transient."""
    if failures < FAILURES_BEFORE_REPAIR:
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(kind, entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=kind,
        translation_placeholders={
            "property": property_name,
            "failures": str(failures),
            "error": error,
        },
    )


def async_clear_failure(hass: HomeAssistant, *, kind: str, entry_id: str) -> None:
    """Remove a Repairs issue after a success."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(kind, entry_id))


def async_clear_all(hass: HomeAssistant, entry_id: str) -> None:
    """Remove every issue for a removed entry."""
    for kind in ISSUE_KINDS:
        async_clear_failure(hass, kind=kind, entry_id=entry_id)


def async_report_output_limit(
    hass: HomeAssistant,
    *,
    entry_id: str,
    property_name: str,
    entity_id: str | None,
    max_tokens: int | None,
    recommended: int,
) -> None:
    """Warn that summaries are being shortened to fit the output limit."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id("output_limit_low", entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="output_limit_low",
        translation_placeholders={
            "property": property_name,
            "entity": entity_id or "the AI Task entity",
            "limit": str(max_tokens) if max_tokens is not None else "unknown",
            "recommended": str(recommended),
        },
    )
