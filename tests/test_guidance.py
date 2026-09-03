"""Tests for persistent owner guidance."""

from __future__ import annotations


def test_guidance_is_trimmed_and_can_be_removed(guidance_module) -> None:
    assert guidance_module.normalize_guidance("  Watch peak power.  ") == (
        "Watch peak power."
    )
    assert guidance_module.normalize_guidance("") == ""
    assert guidance_module.normalize_guidance("   ") == ""


def test_guidance_is_bounded(guidance_module) -> None:
    guidance = "x" * (guidance_module.MAX_GUIDANCE_LENGTH + 100)

    assert len(guidance_module.normalize_guidance(guidance)) == (
        guidance_module.MAX_GUIDANCE_LENGTH
    )


def test_non_text_guidance_is_rejected(guidance_module) -> None:
    assert guidance_module.normalize_guidance(["not", "text"]) == ""
