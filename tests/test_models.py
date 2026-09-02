"""Tests for stored observation models."""

from __future__ import annotations


def test_observation_event_round_trip(models_module) -> None:
    event = models_module.ObservationEvent(
        timestamp="2026-09-02T08:00:00+00:00",
        entity_id="binary_sensor.front_door",
        name="Front door",
        category="access",
        old_state="off",
        new_state="on",
        anomaly={"severity": "watch"},
    )

    assert models_module.ObservationEvent.from_dict(event.as_dict()) == event


def test_summary_ignores_unknown_future_fields(models_module) -> None:
    value = {
        "timestamp": "2026-09-02T08:00:00+00:00",
        "reason": "daily",
        "period_hours": 24,
        "summary": "Everything looks normal.",
        "future_field": "ignored",
    }

    summary = models_module.ObserverSummary.from_dict(value)

    assert summary.severity == "normal"
    assert summary.summary == "Everything looks normal."
