"""Tests for the 0.3 observer logic: ingest, baselines, selection, memory."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

NOW = datetime(2026, 11, 17, 15, 0, tzinfo=UTC)


# --------------------------------------------------------------------- ingest


def test_throttle_folds_short_spike_into_next_stored_event(ingest_module) -> None:
    throttle = ingest_module.NumericThrottle(300)
    assert throttle.offer("sensor.power", NOW, 100.0, anomalous=False) == {}
    assert (
        throttle.offer(
            "sensor.power", NOW + timedelta(seconds=60), 4000.0, anomalous=False
        )
        is None
    )
    assert (
        throttle.offer(
            "sensor.power", NOW + timedelta(seconds=120), 110.0, anomalous=False
        )
        is None
    )
    window = throttle.offer(
        "sensor.power", NOW + timedelta(seconds=301), 105.0, anomalous=False
    )
    assert window == {
        "window_min": 105.0,
        "window_max": 4000.0,
        "window_mean": pytest.approx((4000 + 110 + 105) / 3, rel=1e-3),
        "window_samples": 3,
    }


def test_throttle_stores_anomaly_immediately_once_per_window(ingest_module) -> None:
    throttle = ingest_module.NumericThrottle(300)
    throttle.offer("sensor.power", NOW, 100.0, anomalous=False)
    assert (
        throttle.offer(
            "sensor.power", NOW + timedelta(seconds=10), 9000.0, anomalous=True
        )
        is not None
    )
    assert (
        throttle.offer(
            "sensor.power", NOW + timedelta(seconds=20), 9100.0, anomalous=True
        )
        is None
    )


def test_minutes_since(ingest_module) -> None:
    assert ingest_module.minutes_since(NOW - timedelta(minutes=95), NOW) == 95
    assert ingest_module.minutes_since(None, NOW) is None


# ------------------------------------------------------------------ baselines


def test_baselines_are_split_by_time_of_day(patterns_module) -> None:
    engine = patterns_module.PatternEngine(minimum_samples=5, zscore_threshold=3)
    night = datetime(2026, 1, 1, 3, tzinfo=UTC)
    evening = datetime(2026, 1, 1, 19, tzinfo=UTC)
    for value in (100, 102, 98, 101, 99):
        engine.observe("sensor.power", str(value), night)
    for value in (2000, 2050, 1950, 2020, 1980):
        engine.observe("sensor.power", str(value), evening)

    # 2000 W is normal in the evening but far outside the night baseline.
    assert engine.observe("sensor.power", "2000", evening) is None
    anomaly = engine.observe("sensor.power", "2000", night)
    assert anomaly is not None
    assert anomaly["baseline"] == "night"


def test_baselines_fade_with_half_life(patterns_module) -> None:
    stats = patterns_module.OnlineStats()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for value in (10, 10, 10, 10):
        stats.update(value, start, half_life_days=30)
    stats.update(10, start + timedelta(days=30), half_life_days=30)
    assert stats.count == 5
    assert stats.weight == pytest.approx(4 * 0.5 + 1)


def test_numeric_values_do_not_flood_state_counts(patterns_module) -> None:
    engine = patterns_module.PatternEngine()
    for value in range(50):
        engine.observe("sensor.temp", str(value), NOW)
    assert engine.entities["sensor.temp"]["state_counts"] == {}


def test_pre_decay_records_still_load(patterns_module) -> None:
    engine = patterns_module.PatternEngine(
        {
            "entities": {
                "sensor.old": {
                    "numeric": {"count": 40, "mean": 5.0, "m2": 39.0},
                    "state_counts": {},
                    "hour_counts": [0] * 24,
                    "observations": 40,
                }
            }
        },
        minimum_samples=30,
    )
    stats = engine.entities["sensor.old"]["numeric"]
    assert stats.weight == 40
    assert engine.observe("sensor.old", "50", NOW)["baseline"] == "overall"


# ------------------------------------------------------------------ discovery


@pytest.fixture
def inventory(discovery_module):
    def make(entity_id: str, name: str, **extra):
        return discovery_module.DiscoveryCandidate(
            entity_id=entity_id,
            name=name,
            state="closed",
            domain=entity_id.split(".", 1)[0],
            area_name="Main",
            **extra,
        )

    return [
        make("binary_sensor.front_door", "Front door", device_class="door"),
        make("sensor.water_flow", "Water flow", unit="gal/min"),
        make("cover.hot_tub_cover", "Hot tub cover"),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "| entity_id | category | reason |\n|---|---|---|\n"
        "| binary_sensor.front_door | access | Exterior entry |",
        "- `binary_sensor.front_door` — access — Exterior entry",
        "binary_sensor.front_door; access; Exterior entry",
        '[{"entity_id": "binary_sensor.front_door", "category": "access",'
        ' "reason": "Exterior entry"}]',
        "**binary_sensor.front_door** | access | Exterior entry",
    ],
)
def test_parser_tolerates_format_drift(discovery_module, inventory, text) -> None:
    result = discovery_module.parse_recommendations(text, inventory, confidence=0.8)
    assert [item["entity_id"] for item in result] == ["binary_sensor.front_door"]
    assert result[0]["category"] == "access"


def test_parser_accepts_bare_entity_mentions(discovery_module, inventory) -> None:
    text = "I recommend sensor.water_flow and cover.hot_tub_cover for this home."
    result = discovery_module.parse_recommendations(text, inventory)
    assert {item["entity_id"]: item["category"] for item in result} == {
        "sensor.water_flow": "water",
        "cover.hot_tub_cover": "spa",
    }


def test_parser_accepts_structured_list(discovery_module, inventory) -> None:
    result = discovery_module.parse_recommendations(
        [{"entity_id": "sensor.water_flow", "category": "water", "reason": "Leaks"}],
        inventory,
    )
    assert result[0]["category"] == "water"


# ------------------------------------------------------------------ selection


def test_selection_combines_discovery_pins_labels_and_ignores(
    selection_module,
) -> None:
    index = selection_module.RegistryIndex(
        entity_devices={
            "sensor.a": "dev1",
            "sensor.b": "dev1",
            "sensor.c": "dev2",
            "sensor.d": None,
            "sensor.e": "dev3",
        },
        device_entities={"dev1": ["sensor.a", "sensor.b"]},
        label_monitor={"sensor.d", "sensor.e"},
        label_ignore={"sensor.b"},
    )
    result = selection_module.effective_entities(
        index,
        selection_module.SelectionInputs(
            auto_discovery=True,
            recommendations=[{"entity_id": "sensor.c"}],
            pinned_devices=["dev1"],
            ignored_devices=["dev3"],
        ),
    )
    assert result == ["sensor.a", "sensor.c", "sensor.d"]


def test_discovery_off_keeps_pins_only(selection_module) -> None:
    result = selection_module.effective_entities(
        selection_module.RegistryIndex(),
        selection_module.SelectionInputs(
            auto_discovery=False,
            recommendations=[{"entity_id": "sensor.ai"}],
            pinned_entities=["sensor.pinned"],
        ),
    )
    assert result == ["sensor.pinned"]


def test_labels_map_to_flags(selection_module) -> None:
    assert selection_module.labels_to_flags({"observer"}) == (True, False, None)
    assert selection_module.labels_to_flags({"observer_ignore"}) == (
        False,
        True,
        None,
    )
    assert selection_module.labels_to_flags({"observer_water"}) == (
        True,
        False,
        "water",
    )


def test_category_precedence(selection_module) -> None:
    index = selection_module.RegistryIndex(label_categories={"sensor.x": "water"})
    recs = [{"entity_id": "sensor.x", "category": "energy"}]
    assert (
        selection_module.category_for(
            "sensor.x",
            index=index,
            overrides={"sensor.x": "spa"},
            recommendations=recs,
            fallback="other",
        )
        == "water"
    )
    assert (
        selection_module.category_for(
            "sensor.x",
            index=selection_module.RegistryIndex(),
            overrides={"sensor.x": "spa"},
            recommendations=recs,
            fallback="other",
        )
        == "spa"
    )


# ------------------------------------------------------------------ knowledge


def test_notes_expire_and_are_bounded(knowledge_module) -> None:
    keep = knowledge_module.new_note("Spa heater replaced", "maintenance", NOW)
    temp = knowledge_module.new_note(
        "Guest reported a dripping faucet", "guest_report", NOW, expires_in_days=2
    )
    pruned = knowledge_module.prune_knowledge([keep, temp], NOW + timedelta(days=3))
    assert pruned == [keep]

    many = [knowledge_module.new_note(str(i), "owner", NOW) for i in range(150)]
    assert len(knowledge_module.prune_knowledge(many, NOW)) == 100


def test_recurring_candidate_memories_rank_by_repetition(
    knowledge_module, models_module
) -> None:
    def summary(text: str):
        return models_module.ObserverSummary(
            timestamp=NOW.isoformat(),
            reason="daily",
            period_hours=24,
            summary="ok",
            candidate_memories=text,
        )

    summaries = [
        summary("- Hot tub heats overnight.\n- Dryer runs Sundays"),
        summary("Hot tub heats overnight"),
        summary("Hot tub heats overnight."),
    ]
    result = knowledge_module.recurring_candidate_memories(summaries)
    assert result[0] == {"pattern": "Hot tub heats overnight.", "seen_in_summaries": 3}

    confirmed = knowledge_module.recurring_candidate_memories(
        summaries, exclude={"hot tub heats overnight"}
    )
    assert [item["pattern"] for item in confirmed] == ["Dryer runs Sundays"]


# ------------------------------------------------------------------ occupancy


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("on", "occupied"),
        ("off", "vacant"),
        ("home", "occupied"),
        ("not_home", "vacant"),
        ("2", "occupied"),
        ("0", "vacant"),
        ("unavailable", "unknown"),
    ],
)
def test_occupancy_entity_states(occupancy_module, state, expected) -> None:
    assert occupancy_module.occupancy_from_state(state) == expected


def test_stay_never_carries_ids_or_labels(occupancy_module) -> None:
    stay = occupancy_module.stay_reservation(
        {
            "reservation_id": "HMABC123",
            "label": "Smith family",
            "guest_count": 6,
            "check_in": (NOW - timedelta(hours=2)).isoformat(),
            "check_out": (NOW + timedelta(days=2)).isoformat(),
        },
        NOW,
    )
    assert stay["status"] == "current"
    assert stay["guest_count"] == 6
    serialized = repr(stay)
    assert "HMABC123" not in serialized
    assert "Smith" not in serialized


def test_stay_expires_after_checkout(occupancy_module) -> None:
    stay = {"check_out": (NOW - timedelta(minutes=1)).isoformat()}
    assert occupancy_module.stay_expired(stay, NOW)
    assert not occupancy_module.stay_expired({}, NOW)


def test_occupancy_source_priority(occupancy_module) -> None:
    current = {"status": "current"}
    upcoming = {"status": "upcoming", "starts_in_minutes": 60}
    resolve = occupancy_module.resolve_occupancy
    assert resolve(entity_state="off", stay=current, reservations=[]) == {
        "state": "vacant",
        "source": "occupancy_entity",
    }
    assert resolve(entity_state=None, stay=current, reservations=[])["state"] == (
        "occupied"
    )
    assert resolve(entity_state=None, stay=None, reservations=[upcoming]) == {
        "state": "vacant",
        "source": "reservation_calendar",
    }
    assert resolve(entity_state=None, stay=None, reservations=[])["state"] == (
        "unknown"
    )


# -------------------------------------------------------------------- prompts


def test_prompts_follow_mode_and_occupancy(prompts_module) -> None:
    def prompt(mode: str, occupancy: str) -> str:
        return prompts_module.build_summary_prompt(
            mode=mode, occupancy=occupancy, guidance="", reason="daily", context={}
        )

    rental_occupied = prompt("rental", "occupied")
    assert "short-term rental" in rental_occupied
    assert "misuse" in rental_occupied
    assert "never estimate a head count" in rental_occupied

    rental_vacant = prompt("rental", "vacant")
    assert "hot tub cover open" in rental_vacant
    assert "next arrival" in rental_vacant

    home = prompt("home", "occupied")
    assert "short-term rental" not in home
    assert "water use" in home
    assert "Reservation timing" not in home

    unknown = prompt("home", "unknown")
    assert "If occupied:" in unknown
    assert "If vacant:" in unknown


# ------------------------------------------------------------ registry churn


@pytest.mark.parametrize(
    ("event_type", "data", "expected"),
    [
        (
            "entity_registry_updated",
            {"action": "create", "entity_id": "sensor.new_water_meter"},
            (True, True),
        ),
        (
            "entity_registry_updated",
            {"action": "remove", "entity_id": "binary_sensor.old_door"},
            (True, True),
        ),
        # Network integrations create trackers for every client: ignore them.
        (
            "entity_registry_updated",
            {"action": "create", "entity_id": "device_tracker.guest_phone"},
            (False, False),
        ),
        (
            "entity_registry_updated",
            {
                "action": "update",
                "entity_id": "sensor.power",
                "changes": {"labels": set()},
            },
            (True, False),
        ),
        (
            "entity_registry_updated",
            {
                "action": "update",
                "entity_id": "sensor.power",
                "changes": {"capabilities": {}, "modified_at": "x"},
            },
            (False, False),
        ),
        (
            "entity_registry_updated",
            {
                "action": "update",
                "entity_id": "sensor.new",
                "old_entity_id": "sensor.old",
                "changes": {"entity_id": "sensor.old"},
            },
            (True, True),
        ),
        (
            "device_registry_updated",
            {"action": "update", "device_id": "d", "changes": {"sw_version": "1"}},
            (False, False),
        ),
        (
            "device_registry_updated",
            {"action": "update", "device_id": "d", "changes": {"area_id": None}},
            (True, False),
        ),
        ("label_registry_updated", {"action": "create"}, (True, False)),
    ],
)
def test_registry_change_effect(discovery_module, event_type, data, expected) -> None:
    assert discovery_module.registry_change_effect(event_type, data) == expected


def test_registry_rediscovery_is_daily(discovery_module) -> None:
    allowed = discovery_module.registry_rediscovery_allowed
    assert allowed(None, NOW)
    assert not allowed(NOW - timedelta(hours=5), NOW)
    assert allowed(NOW - timedelta(hours=24), NOW)
