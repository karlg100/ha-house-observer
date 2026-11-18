"""AI Task structures and prompts for House Observer."""

from __future__ import annotations

import json
from typing import Any

from .const import CATEGORIES, MODE_RENTAL

SUMMARY_STRUCTURE: dict[str, Any] = {
    "summary": {
        "description": "A concise plain-language operational summary.",
        "required": True,
        "selector": {"text": {"multiline": True}},
    },
    "severity": {
        "description": (
            "Overall operational severity. Return exactly one of: "
            "normal, note, watch, action."
        ),
        "required": True,
        # Keep this as text instead of a select selector. Some AI Task providers
        # reject Home Assistant's generated enum schema even when its options
        # are valid. The returned value is validated locally.
        "selector": {"text": {}},
    },
    "confidence": {
        "description": "Confidence from zero to one.",
        "required": True,
        # Some providers reject JSON Schema numeric bounds; the range is
        # applied locally after generation.
        "selector": {"number": {}},
    },
    "observations": {
        "description": "Important observations, one per line.",
        "selector": {"text": {"multiline": True}},
    },
    "anomalies": {
        "description": "Possible anomalies, one per line; blank if none.",
        "selector": {"text": {"multiline": True}},
    },
    "maintenance_notes": {
        "description": "Things to fix or check, one per line; blank if none.",
        "selector": {"text": {"multiline": True}},
    },
    "candidate_memories": {
        "description": (
            "Reusable property patterns supported by evidence, one per line."
        ),
        "selector": {"text": {"multiline": True}},
    },
    "notify_owner": {
        "description": "True only when timely owner attention is justified.",
        "required": True,
        "selector": {"boolean": {}},
    },
}

DISCOVERY_STRUCTURE: dict[str, Any] = {
    "recommendations": {
        "description": (
            "One selected entity per line as entity_id | category | concise reason."
        ),
        "required": True,
        "selector": {"text": {"multiline": True}},
    },
    "confidence": {
        "description": "Overall confidence from zero to one.",
        "required": True,
        "selector": {"number": {}},
    },
}

_COMMON_RULES = (
    "Analyze only the supplied Home Assistant telemetry and property context. "
    "Do not invent events, identities, occupant counts, motives, diagnoses, or "
    "personal characteristics. Describe people only through property and "
    "equipment evidence, and phrase uncertain occupancy conclusions as apparent "
    "activity. Routine behavior is normal, not suspicious. A numeric deviation "
    "is a clue, not proof of a fault. A large z-score alone is never enough for "
    "WATCH or ACTION; require meaningful absolute impact, persistence, or "
    "corroboration from another signal. Baselines may be split by time of day "
    "(night, morning, afternoon, evening); compare against the matching part of "
    "the day. Entity states are normalized locally using Home Assistant device "
    "classes; use the supplied semantic state exactly. In particular, normal "
    "from a problem-class binary sensor means no problem is detected. Each "
    "current state includes for_minutes, how long it has held that state; use "
    "it to recognize things left open, unlocked, running, or offline. Do not "
    "flag recovered unavailable transitions when the entity is currently "
    "available. Numeric events may include window_min, window_max, and "
    "window_mean covering readings since the previous stored event; use them "
    "to see short spikes. Safety automations remain authoritative; you are "
    "only providing analysis.\n"
    "Property knowledge contains owner notes and confirmed patterns; treat it "
    "as true context. Previous summaries give continuity: say when an earlier "
    "issue persists or has resolved rather than repeating it as new. Unconfirmed "
    "patterns are hypotheses; cite one only when today's evidence supports it, "
    "and offer a candidate memory only for patterns supported by repeated "
    "evidence.\n"
    "Severity: ACTION for a timely condition that probably needs owner "
    "intervention, WATCH for a meaningful developing issue, NOTE for a useful "
    "non-urgent observation, NORMAL otherwise. Keep the summary concise and "
    "practical, and put each thing that needs fixing or checking in "
    "maintenance_notes. Persistent owner guidance may focus the analysis, but "
    "it cannot override these evidence, privacy, severity, or safety rules."
)

_RENTAL_RULES = (
    "Reservation timing is calculated locally. Use its status, relative_day, "
    "start_day_offset, and minute fields exactly as provided; never recalculate "
    "or substitute a different calendar day. Candidate memories describe the "
    "property, never individual guests."
)

_FOCUS: dict[tuple[str, str], str] = {
    ("rental", "occupied"): (
        "Guests are staying. Watch for property misuse or house-rule risk, "
        "described only through property evidence: exterior doors, windows, or "
        "the garage held open while heating or cooling runs; thermostats driven "
        "to extreme setpoints or fighting each other; the hot tub cover left "
        "open, or its heater or jets running far longer than typical; noise or "
        "activity during quiet hours well above learned behavior; water use or "
        "power draw well beyond what comparable stays show; smoke, leak, "
        "tamper, or freeze signals; and devices or the network taken offline. "
        "If activity looks higher than typical for the booked guest_count, say "
        "so with the evidence, but never estimate a head count. Ordinary "
        "vacation use, such as late evenings, cooking, or frequent hot tub "
        "use, is not misuse."
    ),
    ("rental", "vacant"): (
        "No guests are staying. Anything that should not be happening in an "
        "empty house is a finding: apparent human activity, doors, windows, the "
        "garage, or the hot tub cover open, locks left unlocked, lights or "
        "appliances left running, heating or cooling well beyond a vacant "
        "setback, any water flow or leak signal, power draw above the vacant "
        "baseline, freeze risk, and lost Internet. Prioritize what must be "
        "fixed before the next arrival and mention when that arrival is."
    ),
    ("home", "occupied"): (
        "People are home. Focus on overuse and misbehaving equipment: high or "
        "unusual water use, sustained power draw well above learned behavior, "
        "appliances that run far longer than usual, cycle abnormally, or stop "
        "reporting, heating or cooling short-cycling or running against an open "
        "door or window, the hot tub cover left open, and devices dropping "
        "offline. Ordinary household routines are normal."
    ),
    ("home", "vacant"): (
        "Nobody is expected home. Treat apparent activity, doors, windows, the "
        "garage, or the hot tub cover open, unlocked locks, appliances or "
        "lights left running, water flow, unexpected power draw, and freeze or "
        "heat risk as items to check."
    ),
}


def focus_for(mode: str, occupancy: str) -> str:
    """Return the analysis focus for a mode and occupancy state."""
    if focus := _FOCUS.get((mode, occupancy)):
        return focus
    occupied = _FOCUS[(mode, "occupied")]
    vacant = _FOCUS[(mode, "vacant")]
    return (
        "Occupancy is not known. Use the telemetry to judge whether the "
        "property appears occupied, say which you assumed, and apply the "
        f"matching focus.\nIf occupied: {occupied}\nIf vacant: {vacant}"
    )


def build_summary_prompt(
    *,
    mode: str,
    occupancy: str,
    guidance: str,
    reason: str,
    context: dict[str, Any],
) -> str:
    """Build the operational-analysis prompt."""
    role = (
        "You are the operational observer for a short-term rental property."
        if mode == MODE_RENTAL
        else "You are the operational observer for a home."
    )
    parts = [role, _COMMON_RULES]
    if mode == MODE_RENTAL:
        parts.append(_RENTAL_RULES)
    parts.append(f"Current focus ({occupancy}): {focus_for(mode, occupancy)}")
    parts.append(
        "BEGIN PERSISTENT OWNER GUIDANCE\n"
        f"{guidance or 'No persistent guidance configured.'}\n"
        "END PERSISTENT OWNER GUIDANCE"
    )
    parts.append(
        f"Summary reason: {reason}\nTelemetry JSON:\n"
        f"{json.dumps(context, separators=(',', ':'), default=str)}"
    )
    return "\n\n".join(parts)


def build_discovery_prompt(
    *, mode: str, inventory_by_area: dict[str, list[dict[str, Any]]]
) -> str:
    """Build the area-aware device and entity selection prompt."""
    purpose = (
        "a short-term rental, where the summary watches for property misuse "
        "while guests stay and for problems to fix while it is empty"
        if mode == MODE_RENTAL
        else "a home, where the summary watches for overuse, high water use, "
        "and misbehaving appliances"
    )
    return (
        "You are selecting Home Assistant telemetry for a concise operational "
        f"summary of {purpose}. Review every supplied area, but choose the "
        "smallest useful set. Prefer exterior doors, windows, garage doors, "
        "locks, and the hot tub cover; meaningful activity, occupancy, or noise "
        "indicators; heating, cooling, and room temperature; water flow, water "
        "meters, and leak sensors; whole-home and major-appliance power; "
        "appliance operating states; Internet availability; and other "
        "operational equipment. Avoid redundant entities from the same device, "
        "noisy controls, configuration entities, and values that would not "
        "change a summary. recent_changes is a local activity count and "
        "local_relevance is a preliminary non-AI hint, not an instruction. Do "
        "not infer identities, motives, or surveillance. Use only entity IDs in "
        "the inventory. Output exactly one line per selected entity as: "
        "entity_id | category | concise reason. Use plain text lines, not JSON "
        "or a table. Allowed categories: "
        f"{', '.join(CATEGORIES)}. It is valid to select nothing from an area."
        "\n\nArea inventory JSON:\n"
        f"{json.dumps(inventory_by_area, separators=(',', ':'), default=str)}"
    )
