"""``SchedulingPolicyCreate`` (#61 D2): every value required, the A1 ranges,
an aware optional ``effective_from`` and no unknown field."""

import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.administration.schemas import SchedulingPolicyCreate


def body(**changes: Any) -> dict[str, Any]:
    return {
        "based_on_id": str(uuid.uuid7()),
        "booking_notice_hours": 24,
        "change_notice_hours": 24,
        "response_hours": 48,
        "makeup_after_on_time": True,
        "makeup_after_late": False,
        "makeup_after_no_show": False,
        "makeup_window_days": 30,
        **changes,
    }


def error_paths(data: dict[str, Any]) -> list[tuple[Any, ...]]:
    with pytest.raises(ValidationError) as exc:
        SchedulingPolicyCreate.model_validate(data)
    return [error["loc"] for error in exc.value.errors()]


def test_a_complete_body_without_effective_from_is_valid():
    parsed = SchedulingPolicyCreate.model_validate(body())

    assert parsed.effective_from is None
    assert parsed.response_hours == 48


@pytest.mark.parametrize(
    ("field", "low", "high"),
    [
        ("booking_notice_hours", 0, 336),
        ("change_notice_hours", 0, 336),
        ("response_hours", 1, 168),
        ("makeup_window_days", 1, 365),
    ],
)
def test_each_range_accepts_its_bounds_and_rejects_beyond(field, low, high):
    assert (
        getattr(SchedulingPolicyCreate.model_validate(body(**{field: low})), field)
        == low
    )
    assert (
        getattr(SchedulingPolicyCreate.model_validate(body(**{field: high})), field)
        == high
    )
    assert error_paths(body(**{field: low - 1})) == [(field,)]
    assert error_paths(body(**{field: high + 1})) == [(field,)]


@pytest.mark.parametrize(
    "field",
    [
        "based_on_id",
        "booking_notice_hours",
        "change_notice_hours",
        "response_hours",
        "makeup_after_on_time",
        "makeup_after_late",
        "makeup_after_no_show",
        "makeup_window_days",
    ],
)
def test_every_value_is_required(field):
    data = body()
    del data[field]

    assert error_paths(data) == [(field,)]


def test_an_aware_effective_from_is_accepted_and_a_naive_one_rejected():
    aware = SchedulingPolicyCreate.model_validate(
        body(effective_from="2026-12-01T06:00:00Z")
    )

    assert aware.effective_from is not None
    assert aware.effective_from.utcoffset() is not None
    assert error_paths(body(effective_from="2026-12-01T00:00:00")) == [
        ("effective_from",)
    ]


def test_an_unknown_field_is_rejected():
    assert error_paths(body(version=1)) == [("version",)]
