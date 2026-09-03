"""Pure helpers for persistent House Observer guidance."""

from __future__ import annotations

from typing import Any

MAX_GUIDANCE_LENGTH = 4000


def normalize_guidance(value: Any) -> str:
    """Return trimmed, bounded persistent guidance."""
    if not isinstance(value, str):
        return ""
    guidance = value.strip()
    if len(guidance) <= MAX_GUIDANCE_LENGTH:
        return guidance
    return guidance[:MAX_GUIDANCE_LENGTH]
