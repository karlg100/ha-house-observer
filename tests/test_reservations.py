"""Tests for deterministic, privacy-safe reservation context."""

from __future__ import annotations

from datetime import datetime


def test_next_reservation_is_today_not_tomorrow(reservations_module) -> None:
    now = datetime.fromisoformat("2026-09-03T13:58:35-04:00")
    values = [
        {
            "entity_id": "sensor.rental_event_1",
            "attributes": {
                "start": "2026-09-13T16:00:00-04:00",
                "end": "2026-09-16T11:00:00-04:00",
                "name": "Private guest name",
            },
        },
        {
            "entity_id": "sensor.rental_event_0",
            "attributes": {
                "start": "2026-09-03T14:30:00-04:00",
                "end": "2026-09-07T11:00:00-04:00",
                "phone_number": "private",
                "reservation_url": "private",
            },
        },
    ]

    result = reservations_module.normalize_reservations(values, now)

    assert result[0] == {
        "entity_id": "sensor.rental_event_0",
        "status": "upcoming",
        "start_local": "2026-09-03T14:30:00-04:00",
        "relative_day": "today",
        "start_day_offset": 0,
        "end_local": "2026-09-07T11:00:00-04:00",
        "starts_in_minutes": 32,
        "ends_in_minutes": 5582,
        "sequence": 1,
    }
    assert result[1]["sequence"] == 2
    assert result[1]["relative_day"] == "in 10 days"
    assert "name" not in result[0]
    assert "phone_number" not in result[0]
    assert "reservation_url" not in result[0]


def test_current_reservation_status_uses_start_and_end(reservations_module) -> None:
    now = datetime.fromisoformat("2026-09-04T12:00:00-04:00")
    values = [
        {
            "entity_id": "sensor.rental_event_0",
            "attributes": {
                "start": "2026-09-03T14:30:00-04:00",
                "end": "2026-09-07T11:00:00-04:00",
            },
        }
    ]

    result = reservations_module.normalize_reservations(values, now)

    assert result[0]["status"] == "current"
    assert result[0]["relative_day"] == "yesterday"
    assert result[0]["started_minutes_ago"] == 1290
    assert result[0]["ends_in_minutes"] == 4260


def test_timezone_is_converted_before_relative_day(reservations_module) -> None:
    now = datetime.fromisoformat("2026-09-03T13:58:35-04:00")
    values = [
        {
            "entity_id": "sensor.rental_event_0",
            "attributes": {
                "start": "2026-09-03T18:30:00Z",
                "end": "2026-09-07T15:00:00Z",
            },
        }
    ]

    result = reservations_module.normalize_reservations(values, now)

    assert result[0]["start_local"] == "2026-09-03T14:30:00-04:00"
    assert result[0]["relative_day"] == "today"
    assert result[0]["starts_in_minutes"] == 32


def test_safe_attributes_exclude_private_booking_details(reservations_module) -> None:
    attributes = {
        "start": "2026-09-03T14:30:00-04:00",
        "end": "2026-09-07T11:00:00-04:00",
        "name": "Private guest name",
        "email_address": "private@example.com",
        "phone_number": "555-0100",
        "slot_code": "1234",
        "reservation_url": "https://example.invalid/private-feed",
    }

    assert reservations_module.safe_reservation_attributes(attributes) == {
        "start": "2026-09-03T14:30:00-04:00",
        "end": "2026-09-07T11:00:00-04:00",
    }
