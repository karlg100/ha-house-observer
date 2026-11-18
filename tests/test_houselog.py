"""Tests for the compact house log."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

START = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)
END = START + timedelta(hours=24)


def at(hours: float) -> datetime:
    return START + timedelta(hours=hours)


def series(module, entity_id, name, category, kind, points, unit=None, spread=None):
    return module.Series(
        entity_id=entity_id,
        name=name,
        category=category,
        kind=kind,
        points=[
            module.Point(when, state, attrs or {}) for when, state, attrs in points
        ],
        unit=unit,
        spread=spread,
    )


def test_every_door_change_is_kept_with_durations(houselog_module) -> None:
    door = series(
        houselog_module,
        "binary_sensor.sliding_door",
        "Sliding door",
        "access",
        "discrete",
        [
            (START, "closed", None),
            (at(7.2), "open", None),
            (at(7.2 + 2 / 60), "closed", None),
            (at(17 + 40 / 60), "open", None),
            (at(17 + 52 / 60), "closed", None),
            (at(21.05), "open", None),
            (at(21.05), "closed", None),
        ],
    )
    log = houselog_module.render_house_log([door], START, END)
    line = next(item for item in log.text.splitlines() if item.startswith("Sliding"))
    assert line.count("open ") == 3
    assert "open 17:40-17:52 (12m)" in line
    assert "open 21:03-21:03 (0m)" in line  # a momentary flap is still shown


def test_numeric_spike_keeps_its_own_segment(houselog_module) -> None:
    points = [
        (at(hour / 60), str(1250 + (hour % 3) * 20), None) for hour in range(0, 600)
    ]
    points += [(at(10 + minute / 60), "2600", None) for minute in range(0, 6)]
    points += [(at(10.1 + minute / 60), "1260", None) for minute in range(0, 60)]
    power = series(
        houselog_module,
        "sensor.house",
        "House power",
        "energy",
        "numeric",
        points,
        unit="W",
        spread=200,
    )
    log = houselog_module.render_house_log([power], START, END)
    line = next(
        item for item in log.text.splitlines() if item.startswith("House power")
    )
    assert "10:00-10:06 2.60k" in line
    assert line.count("·") <= 4  # steady stretches collapse


def test_small_sensor_bumps_are_not_hidden(houselog_module) -> None:
    points = [(at(hour), "7.5", None) for hour in range(0, 19)]
    points += [(at(19.5), "16.4", None), (at(19.9), "7.4", None)]
    boiler = series(
        houselog_module,
        "sensor.boiler",
        "Boiler",
        "hvac",
        "numeric",
        points,
        unit="W",
        spread=1.2,
    )
    text = houselog_module.render_house_log([boiler], START, END).text
    assert "19:30-19:54 16.4" in text


def test_thermostat_and_overlap(houselog_module) -> None:
    thermostat = series(
        houselog_module,
        "climate.main",
        "Main Floor",
        "hvac",
        "climate",
        [
            (START, "heat", {"hvac_action": "idle", "temperature": 69.5}),
            (at(17.5), "heat", {"hvac_action": "heating", "temperature": 69.5}),
            (at(18.5), "heat", {"hvac_action": "idle", "temperature": 69.5}),
        ],
    )
    door = series(
        houselog_module,
        "binary_sensor.porch",
        "Porch door",
        "access",
        "discrete",
        [
            (START, "closed", None),
            (at(17 + 40 / 60), "open", None),
            (at(17 + 52 / 60), "closed", None),
        ],
    )
    text = houselog_module.render_house_log([thermostat, door], START, END).text
    assert "heat/heating/69.5 17:30-18:30 (1h)" in text
    assert "Porch door open while Main Floor heating: Tue 17:40-17:52 (12m)" in text


def test_budget_drops_timeline_then_widens_numeric_never_discrete(
    houselog_module,
) -> None:
    noisy = series(
        houselog_module,
        "sensor.noisy",
        "Noisy",
        "energy",
        "numeric",
        [
            (at(minute / 60), str(100 + (minute % 2) * 400), None)
            for minute in range(1440)
        ],
        unit="W",
    )
    door = series(
        houselog_module,
        "binary_sensor.door",
        "Door",
        "access",
        "discrete",
        [(START, "closed", None), (at(8), "open", None), (at(8.1), "closed", None)],
    )
    full = houselog_module.render_house_log([noisy, door], START, END)
    small = houselog_module.render_house_log(
        [noisy, door], START, END, budget_chars=len(full.text) // 3
    )
    assert not small.timeline_included
    assert "Door: closed" in small.text and "open 08:00-08:06 (6m)" in small.text
    assert small.tolerance_multiplier > 1 or small.truncated


def test_day_marker_appears_when_day_changes(houselog_module) -> None:
    start = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)
    door = series(
        houselog_module,
        "cover.garage",
        "Garage",
        "access",
        "discrete",
        [(start, "closed", None), (start + timedelta(hours=11), "open", None)],
    )
    text = houselog_module.render_house_log(
        [door], start, start + timedelta(hours=24)
    ).text
    assert "closed Mon 20:00-Tue 07:00 (11h)" in text


def test_events_list_times(houselog_module) -> None:
    motion = series(
        houselog_module,
        "event.driveway",
        "Driveway motion",
        "activity",
        "event",
        [(at(1), "x", None), (at(13.25), "y", None)],
    )
    text = houselog_module.render_house_log([motion], START, END).text
    assert "Driveway motion: 2 events at Tue 01:00, 13:15" in text


def test_utc_points_are_shown_in_local_time(houselog_module) -> None:
    from datetime import timezone

    eastern = timezone(timedelta(hours=-5))
    start = datetime(2026, 11, 17, 0, 0, tzinfo=eastern)
    # 21:30 UTC is 16:30 in UTC-5, inside a 4-8 PM peak window.
    door = series(
        houselog_module,
        "binary_sensor.door",
        "Door",
        "access",
        "discrete",
        [
            (start, "closed", None),
            (datetime(2026, 11, 17, 21, 30, tzinfo=UTC), "open", None),
            (datetime(2026, 11, 17, 21, 45, tzinfo=UTC), "closed", None),
        ],
    )
    text = houselog_module.render_house_log(
        [door], start, start + timedelta(hours=24)
    ).text
    assert "open 16:30-16:45 (15m)" in text
    assert "21:30" not in text
