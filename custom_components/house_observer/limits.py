"""Detect the AI Task's output limit and size replies to fit it."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

FULL_REPLY_TOKENS = 8000
_DEFAULT_NAMES = (
    "RECOMMENDED_AI_TASK_MAX_TOKENS",
    "RECOMMENDED_MAX_TOKENS",
)
_DEFAULT_DICTS = ("RECOMMENDED_AI_TASK_OPTIONS", "RECOMMENDED_OPTIONS", "DEFAULT")
# Recommended AI Task output limits of common providers, used when the
# provider's constants cannot be imported.
_KNOWN_DEFAULTS = {
    "anthropic": 3000,
    "openai_conversation": 3000,
    "google_generative_ai_conversation": 19400,
}


@dataclass(slots=True, frozen=True)
class OutputLimit:
    """The output tokens a provider will produce for one reply."""

    entity_id: str | None
    max_tokens: int | None  # None when the provider does not say

    @property
    def compacted(self) -> bool:
        """Whether replies must be shorter than a full summary."""
        return self.max_tokens is not None and self.max_tokens < FULL_REPLY_TOKENS


@dataclass(slots=True, frozen=True)
class ReplyBudget:
    """How long each part of the summary may be."""

    summary_words: int
    list_lines: int
    memory_lines: int


def reply_budget(limit: OutputLimit) -> ReplyBudget:
    """Scale reply length to the output limit, leaving room for overhead."""
    if limit.max_tokens is None:
        return ReplyBudget(300, 8, 4)
    tokens = limit.max_tokens
    return ReplyBudget(
        summary_words=max(120, min(500, int(tokens * 0.12))),
        list_lines=max(3, min(15, tokens // 400)),
        memory_lines=max(2, min(6, tokens // 1500)),
    )


def resolve_ai_task_entity(hass: HomeAssistant, configured: str | None) -> str | None:
    """Return the configured AI Task entity, or Home Assistant's preferred one."""
    if configured:
        return configured
    try:
        from homeassistant.components.ai_task.const import (  # noqa: PLC0415
            DATA_PREFERENCES,
        )
    except ImportError:
        return None
    preferences = hass.data.get(DATA_PREFERENCES)
    return getattr(preferences, "gen_data_entity_id", None)


def _provider_default(domain: str) -> int | None:
    # Only read constants the provider already loaded: importing a module from
    # the event loop would block it.
    const = sys.modules.get(f"homeassistant.components.{domain}.const")
    if const is None:
        return _KNOWN_DEFAULTS.get(domain)
    for name in _DEFAULT_NAMES:
        if isinstance(value := getattr(const, name, None), int):
            return value
    for name in _DEFAULT_DICTS:
        values = getattr(const, name, None)
        if isinstance(values, dict) and isinstance(values.get("max_tokens"), int):
            return values["max_tokens"]
    return _KNOWN_DEFAULTS.get(domain)


def output_limit_from_options(domain: str, options: dict[str, Any]) -> int | None:
    """Read max_tokens from AI Task options, minus any thinking budget."""
    max_tokens = options.get("max_tokens")
    if not isinstance(max_tokens, int):
        max_tokens = _provider_default(domain)
    if max_tokens is None:
        return None
    thinking = options.get("thinking_budget")
    if isinstance(thinking, int) and thinking > 0:
        max_tokens -= thinking
    return max(0, max_tokens)


def detect_output_limit(hass: HomeAssistant, configured: str | None) -> OutputLimit:
    """Find the reply limit of the AI Task entity House Observer will use."""
    entity_id = resolve_ai_task_entity(hass, configured)
    if not entity_id:
        return OutputLimit(None, None)
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None or entry.config_entry_id is None:
        return OutputLimit(entity_id, None)
    config_entry = hass.config_entries.async_get_entry(entry.config_entry_id)
    if config_entry is None:
        return OutputLimit(entity_id, None)
    options: dict[str, Any] = dict(config_entry.options)
    subentry_id = getattr(entry, "config_subentry_id", None)
    if subentry_id and (subentry := config_entry.subentries.get(subentry_id)):
        options = {**options, **subentry.data}
    return OutputLimit(
        entity_id, output_limit_from_options(config_entry.domain, options)
    )
