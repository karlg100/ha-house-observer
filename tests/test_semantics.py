"""Tests for deterministic Home Assistant state semantics."""

from __future__ import annotations


def test_problem_sensor_off_is_normal(semantics_module) -> None:
    assert (
        semantics_module.semantic_state(
            "binary_sensor.raspberry_pi_power_status",
            "off",
            {"device_class": "problem"},
        )
        == "normal"
    )


def test_problem_sensor_on_means_problem_detected(semantics_module) -> None:
    assert (
        semantics_module.semantic_state(
            "binary_sensor.raspberry_pi_power_status",
            "on",
            {"device_class": "problem"},
        )
        == "problem_detected"
    )


def test_access_and_connectivity_semantics(semantics_module) -> None:
    assert (
        semantics_module.semantic_state(
            "binary_sensor.front_door", "off", {"device_class": "door"}
        )
        == "closed"
    )
    assert (
        semantics_module.semantic_state(
            "binary_sensor.router", "off", {"device_class": "connectivity"}
        )
        == "disconnected"
    )


def test_nonbinary_and_unavailable_states_are_unchanged(semantics_module) -> None:
    assert semantics_module.semantic_state("sensor.power", "349", {}) == "349"
    assert (
        semantics_module.semantic_state(
            "binary_sensor.front_door", "unavailable", {"device_class": "door"}
        )
        == "unavailable"
    )
