"""Tests for explainable baseline learning."""

from __future__ import annotations

import math
from datetime import UTC, datetime


def test_online_statistics(patterns_module) -> None:
    stats = patterns_module.OnlineStats()
    for value in (10.0, 11.0, 12.0):
        stats.update(value)

    assert stats.count == 3
    assert stats.mean == 11.0
    assert math.isclose(stats.standard_deviation, 1.0)
    assert stats.minimum == 10.0
    assert stats.maximum == 12.0


def test_numeric_value_rejects_invalid_states(patterns_module) -> None:
    assert patterns_module.numeric_value("12.5") == 12.5
    assert patterns_module.numeric_value("unknown") is None
    assert patterns_module.numeric_value(float("nan")) is None
    assert patterns_module.numeric_value(True) is None


def test_anomaly_compares_before_learning_new_value(patterns_module) -> None:
    engine = patterns_module.PatternEngine(minimum_samples=5, zscore_threshold=3.0)
    when = datetime(2026, 1, 1, 20, tzinfo=UTC)
    for value in (99.8, 100.0, 100.1, 99.9, 100.2):
        assert engine.observe("sensor.spa_temperature", str(value), when) is None

    anomaly = engine.observe("sensor.spa_temperature", "90", when)

    assert anomaly is not None
    assert anomaly["kind"] == "numeric_deviation"
    assert anomaly["samples"] == 5
    assert anomaly["severity"] == "action"


def test_pattern_round_trip(patterns_module) -> None:
    engine = patterns_module.PatternEngine(minimum_samples=2)
    when = datetime(2026, 1, 1, 7, tzinfo=UTC)
    engine.observe("binary_sensor.front_door", "on", when)
    engine.observe("binary_sensor.front_door", "off", when)

    restored = patterns_module.PatternEngine(engine.as_dict(), minimum_samples=2)

    assert restored.learned_entity_count == 1
    context = restored.baseline_context()
    assert context[0]["entity_id"] == "binary_sensor.front_door"
    assert context[0]["common_states"][0][1] == 1


def test_constant_numeric_baseline_detects_first_change(patterns_module) -> None:
    engine = patterns_module.PatternEngine(minimum_samples=5, zscore_threshold=3.0)
    when = datetime(2026, 1, 1, 20, tzinfo=UTC)
    for _ in range(5):
        assert engine.observe("sensor.spa_setpoint", "102", when) is None

    anomaly = engine.observe("sensor.spa_setpoint", "98", when)

    assert anomaly is not None
    assert anomaly["kind"] == "constant_baseline_break"
    assert anomaly["samples"] == 5
