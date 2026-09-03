"""Tests for privacy-conscious entity discovery."""

from __future__ import annotations


def _candidate(discovery_module, entity_id: str, **values):
    return discovery_module.DiscoveryCandidate(
        entity_id=entity_id,
        name=values.pop("name", entity_id),
        state=values.pop("state", "off"),
        domain=entity_id.split(".", 1)[0],
        **values,
    )


def test_private_domains_are_never_candidates(discovery_module) -> None:
    person = _candidate(discovery_module, "person.someone")
    camera = _candidate(discovery_module, "camera.living_room")

    assert discovery_module.relevance_score(person) == 0
    assert discovery_module.relevance_score(camera) == 0
    assert discovery_module.eligible_candidates([person, camera]) == []


def test_personal_mobile_device_sensors_are_not_discovered(discovery_module) -> None:
    phone_battery = _candidate(
        discovery_module,
        "sensor.someone_iphone_battery",
        name="Someone iPhone Battery",
        device_class="battery",
    )

    assert discovery_module.relevance_score(phone_battery) == 0


def test_operational_entities_rank_highly(discovery_module) -> None:
    thermostat = _candidate(
        discovery_module,
        "climate.main_thermostat",
        name="Main thermostat",
        area_name="Living room",
    )
    front_door = _candidate(
        discovery_module,
        "binary_sensor.front_door",
        name="Front door",
        device_class="door",
        area_name="Entry",
    )

    assert discovery_module.relevance_score(thermostat) >= 70
    assert discovery_module.relevance_score(front_door) >= 70


def test_ai_output_is_validated_and_deduplicated(discovery_module) -> None:
    candidates = [
        _candidate(
            discovery_module,
            "binary_sensor.front_door",
            name="Front door",
            device_class="door",
        )
    ]
    result = discovery_module.parse_recommendations(
        "\n".join(
            (
                "binary_sensor.front_door | access | Shows exterior entry activity",
                "sensor.hallucinated | energy | Not in inventory",
                "binary_sensor.front_door | other | Duplicate",
            )
        ),
        candidates,
        confidence=1.7,
    )

    assert len(result) == 1
    assert result[0]["entity_id"] == "binary_sensor.front_door"
    assert result[0]["category"] == "access"
    assert result[0]["confidence"] == 1.0


def test_invalid_category_uses_local_inference(discovery_module) -> None:
    candidate = _candidate(
        discovery_module,
        "sensor.spa_temperature",
        name="Spa water temperature",
    )
    result = discovery_module.parse_recommendations(
        "sensor.spa_temperature | made_up | Useful spa condition",
        [candidate],
    )

    assert result[0]["category"] == "spa"


def test_fallback_is_deliberately_conservative(discovery_module) -> None:
    candidates = [
        _candidate(discovery_module, "sensor.random_value", name="Random value"),
        _candidate(
            discovery_module,
            "sensor.starlink_latency",
            name="Starlink latency",
            device_class="duration",
        ),
    ]

    result = discovery_module.fallback_recommendations(candidates)

    assert [item["entity_id"] for item in result] == ["sensor.starlink_latency"]


def test_inventory_limit_keeps_each_area_represented(discovery_module) -> None:
    candidates = [
        _candidate(
            discovery_module,
            f"climate.area_{area}_{index}",
            area_name=f"Area {area}",
        )
        for area in range(3)
        for index in range(4)
    ]

    selected = discovery_module.eligible_candidates(candidates, limit=6)

    assert {item.area_name for item in selected} == {"Area 0", "Area 1", "Area 2"}
