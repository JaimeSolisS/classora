"""Classroom request bodies (#39 B3-B5): capacity bounds, window shape and
the overlap check with the index of the offending item."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.catalog.schemas import (
    AvailabilityWindow,
    ClassroomCreate,
    ClassroomUpdate,
    window_overlaps,
)


def window(weekday: int, starts_at: str, ends_at: str) -> dict[str, Any]:
    return {"weekday": weekday, "starts_at": starts_at, "ends_at": ends_at}


def errors(body: dict[str, Any], model: Any = ClassroomCreate) -> list[Any]:
    with pytest.raises(ValidationError) as exc:
        model.model_validate(body)
    return [(e["loc"], e["type"]) for e in exc.value.errors()]


def test_a_room_without_windows_is_valid_and_trimmed():
    body = ClassroomCreate.model_validate({"name": "  Aula 1 ", "capacity": 12})

    assert (body.name, body.capacity, body.availability) == ("Aula 1", 12, [])


@pytest.mark.parametrize("capacity", [1, 500])
def test_capacity_bounds_are_accepted(capacity):
    assert ClassroomCreate(name="Aula", capacity=capacity).capacity == capacity


@pytest.mark.parametrize("capacity", [0, -1, 501])
def test_capacity_outside_1_to_500_is_rejected(capacity):
    assert errors({"name": "Aula", "capacity": capacity})[0][0] == ("capacity",)


def test_touching_windows_and_other_weekdays_are_accepted():
    body = ClassroomCreate.model_validate(
        {
            "name": "Aula",
            "capacity": 10,
            "availability": [
                window(1, "09:00", "12:00"),
                window(1, "12:00", "14:00"),
                window(2, "09:00", "12:00"),
            ],
        }
    )

    assert len(body.availability) == 3


def test_an_overlap_is_reported_on_the_later_item():
    found = errors(
        {
            "name": "Aula",
            "capacity": 10,
            "availability": [
                window(1, "09:00", "12:00"),
                window(1, "12:00", "14:00"),
                window(1, "11:00", "13:00"),
            ],
        }
    )

    assert found == [(("availability", 2, "starts_at"), "window_overlap")]


def test_identical_and_contained_windows_overlap():
    windows = [
        AvailabilityWindow(weekday=3, starts_at="09:00", ends_at="18:00"),
        AvailabilityWindow(weekday=3, starts_at="10:00", ends_at="11:00"),
        AvailabilityWindow(weekday=3, starts_at="09:00", ends_at="18:00"),
        AvailabilityWindow(weekday=4, starts_at="10:00", ends_at="11:00"),
    ]

    assert window_overlaps(windows) == [1, 2]


@pytest.mark.parametrize(
    ("item", "loc"),
    [
        (window(1, "12:00", "12:00"), ("availability", 0, "ends_at")),
        (window(1, "13:00", "12:00"), ("availability", 0, "ends_at")),
        (window(0, "09:00", "12:00"), ("availability", 0, "weekday")),
        (window(8, "09:00", "12:00"), ("availability", 0, "weekday")),
        (window(1, "9:00", "12:00"), ("availability", 0, "starts_at")),
        (window(1, "09:00:00", "12:00"), ("availability", 0, "starts_at")),
        (window(1, "09:00", "24:00"), ("availability", 0, "ends_at")),
        ({**window(1, "09:00", "12:00"), "room": 1}, ("availability", 0, "room")),
    ],
)
def test_invalid_windows_are_rejected_on_their_field(item, loc):
    found = errors({"name": "Aula", "capacity": 10, "availability": [item]})

    assert found[0][0] == loc


def test_at_most_28_windows():
    many = [
        window(1 + i % 7, f"{8 + i // 7:02d}:00", f"{8 + i // 7:02d}:30")
        for i in range(29)
    ]

    assert errors({"name": "Aula", "capacity": 10, "availability": many}) == [
        (("availability",), "too_long")
    ]
    ClassroomCreate.model_validate(
        {"name": "Aula", "capacity": 10, "availability": many[:28]}
    )


@pytest.mark.parametrize("field", ["name", "capacity", "is_active", "availability"])
def test_null_is_rejected_in_an_update(field):
    assert errors({"version": 1, field: None}, ClassroomUpdate)[0][0] == (field,)


def test_an_update_needs_a_version_and_forbids_unknown_fields():
    assert errors({"name": "Aula"}, ClassroomUpdate) == [(("version",), "missing")]
    assert errors({"version": 1, "equipment": []}, ClassroomUpdate) == [
        (("equipment",), "extra_forbidden")
    ]


def test_an_update_may_clear_the_windows():
    body = ClassroomUpdate.model_validate({"version": 2, "availability": []})

    assert body.availability == []
    assert body.model_fields_set == {"version", "availability"}
