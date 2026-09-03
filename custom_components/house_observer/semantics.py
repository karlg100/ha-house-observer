"""Deterministic Home Assistant state semantics for AI context."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_BINARY_LABELS: dict[str, tuple[str, str]] = {
    "battery": ("normal", "low"),
    "battery_charging": ("not_charging", "charging"),
    "carbon_monoxide": ("clear", "carbon_monoxide_detected"),
    "cold": ("normal", "cold"),
    "connectivity": ("disconnected", "connected"),
    "door": ("closed", "open"),
    "garage_door": ("closed", "open"),
    "gas": ("clear", "gas_detected"),
    "heat": ("normal", "hot"),
    "light": ("clear", "light_detected"),
    "lock": ("locked", "unlocked"),
    "moisture": ("dry", "wet"),
    "motion": ("clear", "motion_detected"),
    "moving": ("stopped", "moving"),
    "occupancy": ("unoccupied", "occupied"),
    "opening": ("closed", "open"),
    "plug": ("unplugged", "plugged"),
    "power": ("powered_off", "powered_on"),
    "presence": ("away", "present"),
    "problem": ("normal", "problem_detected"),
    "running": ("not_running", "running"),
    "safety": ("safe", "unsafe"),
    "smoke": ("clear", "smoke_detected"),
    "sound": ("quiet", "sound_detected"),
    "tamper": ("clear", "tampering_detected"),
    "update": ("up_to_date", "update_available"),
    "vibration": ("clear", "vibration_detected"),
    "window": ("closed", "open"),
}


def semantic_state(
    entity_id: str, state: Any, attributes: Mapping[str, Any]
) -> str:
    """Translate binary sensor on/off into device-class-specific meaning."""
    raw_state = str(state)
    if not entity_id.startswith("binary_sensor.") or raw_state not in {"on", "off"}:
        return raw_state
    device_class = str(attributes.get("device_class") or "")
    off_label, on_label = _BINARY_LABELS.get(
        device_class, ("inactive", "active")
    )
    return on_label if raw_state == "on" else off_label
