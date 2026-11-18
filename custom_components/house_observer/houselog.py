"""Render a day of telemetry as a compact, lossless-where-it-matters log.

The log is what the model reads instead of raw state rows:

- Discrete entities (doors, locks, covers, appliances, occupancy) keep every
  change as an interval with its duration.
- Numeric entities are split at change points: a new segment starts only when
  the value leaves the sensor's normal variation, so quiet sensors take one
  line while spikes keep their own segments.
- Thermostats keep every mode, action, and setpoint change.
- Overlaps between open doors and active heating or cooling are computed here
  so the model does not have to cross-reference timestamps.

When the text exceeds its budget the timeline is dropped first, then numeric
tolerance is widened. Discrete intervals are never dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

UNAVAILABLE = {"unavailable", "unknown"}
OPEN_STATES = {"open", "opening", "unlocked", "unlocking", "jammed"}
ACTIVE_HVAC = {"heating", "cooling", "preheating", "defrosting", "drying"}
SECTION_ORDER = (
    "access",
    "spa",
    "water",
    "appliance",
    "hvac",
    "energy",
    "occupancy",
    "activity",
    "network",
    "other",
)
DEFAULT_BUDGET_CHARS = 60_000
MAX_TIMELINE_ENTRIES = 400
MAX_OVERLAPS = 40

_UNIT_FLOORS = {
    "W": 3.0,
    "kW": 0.003,
    "°F": 0.3,
    "°C": 0.2,
    "%": 1.0,
    "gal/min": 0.05,
    "L/min": 0.2,
}


@dataclass(slots=True)
class Point:
    """One recorded state."""

    when: datetime
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Series:
    """The history of one entity inside the window."""

    entity_id: str
    name: str
    category: str
    kind: str  # numeric, discrete, event, climate
    points: list[Point]
    unit: str | None = None
    spread: float | None = None  # learned standard deviation, if any


@dataclass(slots=True)
class HouseLog:
    """Rendered log and how it was produced."""

    text: str
    entity_count: int
    raw_points: int
    tolerance_multiplier: float
    timeline_included: bool
    truncated: bool


@dataclass(slots=True)
class _Interval:
    state: str
    start: datetime
    end: datetime


@dataclass(slots=True)
class _Segment:
    start: datetime
    end: datetime
    low: float
    high: float
    total: float = 0.0
    count: int = 0
    unavailable: bool = False


# ---------------------------------------------------------------- formatting


class _Clock:
    """Format times as HH:MM, adding the weekday whenever the day changes."""

    def __init__(self) -> None:
        self._day: tuple[int, int, int] | None = None

    def __call__(self, when: datetime) -> str:
        day = (when.year, when.month, when.day)
        text = when.strftime("%H:%M")
        if day != self._day:
            self._day = day
            return f"{when.strftime('%a')} {text}"
        return text


def duration(delta: timedelta) -> str:
    """Return a short duration such as 3m or 2h05."""
    minutes = max(0, round(delta.total_seconds() / 60))
    if minutes < 60:
        return f"{minutes}m"
    hours, rest = divmod(minutes, 60)
    return f"{hours}h{rest:02d}" if rest else f"{hours}h"


def number(value: float) -> str:
    """Return a compact number: 1.25k, 285, 69.8, 0.42."""
    magnitude = abs(value)
    if magnitude >= 1000:
        return f"{value / 1000:.2f}k"
    if magnitude >= 100:
        return f"{value:.0f}"
    if magnitude >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}".rstrip("0").rstrip(".") or "0"


def _span(low: float, high: float) -> str:
    text_low, text_high = number(low), number(high)
    return text_low if text_low == text_high else f"{text_low}..{text_high}"


# ----------------------------------------------------------------- shaping


def intervals(points: list[Point], start: datetime, end: datetime) -> list[_Interval]:
    """Collapse repeated states and turn changes into closed intervals."""
    output: list[_Interval] = []
    for point in points:
        when = max(point.when, start)
        if output and output[-1].state == point.state:
            continue
        if output:
            output[-1].end = when
        output.append(_Interval(point.state, when, end))
    return output


def tolerance(
    unit: str | None, spread: float | None, mean: float, scale: float
) -> float:
    """Return half the allowed range of one numeric segment."""
    base = max(
        (spread or 0.0) * 0.25,
        abs(mean) * 0.04,
        _UNIT_FLOORS.get(unit or "", 0.0),
    )
    return max(base, 1e-9) * scale


def segments(series: Series, end: datetime, scale: float) -> list[_Segment]:
    """Split a numeric series wherever it leaves its normal variation."""
    output: list[_Segment] = []
    current: _Segment | None = None
    for point in series.points:
        if point.state in UNAVAILABLE:
            if current is not None and not current.unavailable:
                current.end = point.when
                output.append(current)
                current = _Segment(point.when, end, 0, 0, unavailable=True)
            elif current is None:
                current = _Segment(point.when, end, 0, 0, unavailable=True)
            continue
        try:
            value = float(point.state)
        except ValueError:
            continue
        if current is None or current.unavailable:
            if current is not None:
                current.end = point.when
                output.append(current)
            current = _Segment(point.when, end, value, value, value, 1)
            continue
        low, high = min(current.low, value), max(current.high, value)
        allowed = tolerance(
            series.unit, series.spread, current.total / current.count, scale
        )
        if high - low > 2 * allowed:
            current.end = point.when
            output.append(current)
            current = _Segment(point.when, end, value, value, value, 1)
        else:
            current.low, current.high = low, high
            current.total += value
            current.count += 1
    if current is not None:
        current.end = end
        output.append(current)
    return output


def _climate_points(points: list[Point]) -> list[Point]:
    """Reduce thermostat rows to mode/action/setpoint states."""
    output = []
    for point in points:
        if point.state in UNAVAILABLE:
            output.append(Point(point.when, point.state))
            continue
        attrs = point.attributes
        setpoint = attrs.get("temperature")
        if setpoint is None and attrs.get("target_temp_low") is not None:
            setpoint = f"{attrs.get('target_temp_low')}-{attrs.get('target_temp_high')}"
        label = "/".join(
            str(part)
            for part in (point.state, attrs.get("hvac_action") or "-", setpoint)
            if part is not None
        )
        output.append(Point(point.when, label))
    return output


# --------------------------------------------------------------- rendering


def _discrete_line(series: Series, start: datetime, end: datetime) -> str:
    clock = _Clock()
    parts = []
    spans = intervals(series.points, start, end)
    for index, item in enumerate(spans):
        closing = "now" if index == len(spans) - 1 else clock(item.end)
        parts.append(
            f"{item.state} {clock(item.start)}-{closing} "
            f"({duration(item.end - item.start)})"
        )
    return f"{series.name}: " + " · ".join(parts)


def _event_line(series: Series) -> str:
    clock = _Clock()
    times = [
        clock(point.when) for point in series.points if point.state not in UNAVAILABLE
    ]
    return f"{series.name}: {len(times)} events" + (
        f" at {', '.join(times)}" if times else ""
    )


def _numeric_line(series: Series, end: datetime, scale: float) -> str:
    clock = _Clock()
    parts = []
    for segment in segments(series, end, scale):
        span = (
            "unavailable" if segment.unavailable else _span(segment.low, segment.high)
        )
        parts.append(f"{clock(segment.start)}-{clock(segment.end)} {span}")
    unit = f" ({series.unit})" if series.unit else ""
    return f"{series.name}{unit}: " + " · ".join(parts)


def _active_intervals(
    series: Series, start: datetime, end: datetime, wanted: set[str]
) -> list[_Interval]:
    return [
        item
        for item in intervals(series.points, start, end)
        if item.state.split("/")[0 if series.kind != "climate" else 1] in wanted
    ]


def overlaps(series_list: list[Series], start: datetime, end: datetime) -> list[str]:
    """Open doors, windows, or covers while heating or cooling was running."""
    open_spans = [
        (series, item)
        for series in series_list
        if series.kind == "discrete" and series.category in {"access", "spa"}
        for item in _active_intervals(series, start, end, OPEN_STATES)
    ]
    hvac_spans = [
        (series, item)
        for series in series_list
        if series.kind == "climate"
        for item in _active_intervals(series, start, end, ACTIVE_HVAC)
    ]
    lines: list[str] = []
    for door, opened in open_spans:
        for unit, running in hvac_spans:
            begin, finish = (
                max(opened.start, running.start),
                min(opened.end, running.end),
            )
            if finish - begin >= timedelta(minutes=1):
                clock = _Clock()
                action = running.state.split("/")[1]
                lines.append(
                    f"{door.name} {opened.state} while {unit.name} {action}: "
                    f"{clock(begin)}-{clock(finish)} ({duration(finish - begin)})"
                )
    return lines[:MAX_OVERLAPS]


def timeline(series_list: list[Series], start: datetime) -> list[str]:
    """Chronological changes of discrete entities, so co-occurrence is visible."""
    entries: list[tuple[datetime, str]] = []
    for series in series_list:
        if series.kind == "event":
            entries.extend(
                (point.when, f"{series.name} event")
                for point in series.points
                if point.when >= start and point.state not in UNAVAILABLE
            )
        elif series.kind == "discrete":
            previous = None
            for point in series.points:
                if point.state != previous and point.when > start:
                    entries.append((point.when, f"{series.name} {point.state}"))
                previous = point.state
    entries.sort(key=lambda pair: pair[0])
    clock = _Clock()
    return [f"{clock(when)} {text}" for when, text in entries[:MAX_TIMELINE_ENTRIES]]


def _render(
    series_list: list[Series],
    start: datetime,
    end: datetime,
    *,
    scale: float,
    include_timeline: bool,
) -> str:
    by_section: dict[str, list[str]] = {}
    for series in sorted(series_list, key=lambda item: item.name.lower()):
        if series.kind == "numeric":
            line = _numeric_line(series, end, scale)
        elif series.kind == "event":
            line = _event_line(series)
        elif series.kind == "climate":
            line = _discrete_line(
                Series(
                    series.entity_id,
                    f"{series.name} [mode/action/setpoint]",
                    series.category,
                    "climate",
                    _climate_points(series.points),
                ),
                start,
                end,
            )
        else:
            line = _discrete_line(series, start, end)
        section = series.category if series.category in SECTION_ORDER else "other"
        by_section.setdefault(section, []).append(line)

    clock = _Clock()
    hours = (end - start).total_seconds() / 3600
    blocks = [
        f"HOUSE LOG {clock(start)} to {clock(end)} ({hours:.0f}h, local time). "
        "Discrete: state start-end (duration), every change kept. Numeric: "
        "start-end low..high, a new segment begins whenever the value leaves "
        "its normal variation. Thermostats: mode/action/setpoint."
    ]
    for section in SECTION_ORDER:
        if lines := by_section.get(section):
            blocks.append(f"## {section}\n" + "\n".join(lines))
    if lines := overlaps(
        [
            Series(
                item.entity_id,
                item.name,
                item.category,
                item.kind,
                _climate_points(item.points) if item.kind == "climate" else item.points,
            )
            for item in series_list
        ],
        start,
        end,
    ):
        blocks.append("## overlaps\n" + "\n".join(lines))
    if include_timeline and (lines := timeline(series_list, start)):
        blocks.append("## timeline\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def render_house_log(
    series_list: list[Series],
    start: datetime,
    end: datetime,
    *,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> HouseLog:
    """Render the log within a character budget."""
    raw_points = sum(len(series.points) for series in series_list)
    include_timeline = True
    scale = 1.0
    text = _render(series_list, start, end, scale=scale, include_timeline=True)
    if len(text) > budget_chars:
        include_timeline = False
        text = _render(series_list, start, end, scale=scale, include_timeline=False)
    while len(text) > budget_chars and scale < 16:
        scale *= 2
        text = _render(series_list, start, end, scale=scale, include_timeline=False)
    truncated = False
    if len(text) > budget_chars:
        truncated = True
        text = (
            text[: budget_chars - 80]
            + "\n…log truncated; request readings for any entity not shown in full."
        )
    return HouseLog(
        text=text,
        entity_count=len(series_list),
        raw_points=raw_points,
        tolerance_multiplier=scale,
        timeline_included=include_timeline,
        truncated=truncated,
    )
