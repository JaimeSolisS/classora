"""Series overrides: the per-occurrence decision and the pairs body (#236
B1-B3, C1, I5)."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.scheduling.conflicts import HARD, SOFT, WARNING, Conflict
from app.modules.scheduling.overrides import (
    OccurrenceConflict,
    OverriddenOccurrence,
    SeriesOverrideDecision,
    evaluate_override,
    evaluate_series_override,
)
from app.modules.scheduling.schemas import (
    ClassSeriesCreate,
    ClassSeriesEdit,
    ConflictOverrideCreate,
    SeriesOverride,
    SessionOverride,
)

pytestmark = pytest.mark.unit

REASON = "Acordado con la familia"
MX = timezone(timedelta(hours=-6))
FIRST = datetime(2031, 1, 12, 16, 0, tzinfo=UTC)
SECOND = FIRST + timedelta(weeks=1)
THIRD = FIRST + timedelta(weeks=2)

HOURS = Conflict("outside_operating_hours", {"date": "2031-01-12"})
UNQUALIFIED = Conflict("tutor_not_qualified", {"subject_id": "s"})
NOTICE = Conflict("notice_window", {"booking_notice_hours": 24})
TUTOR = Conflict("tutor_overlap", {"class_session_ids": []})


def at(start: datetime, *conflicts: Conflict) -> list[OccurrenceConflict]:
    return [OccurrenceConflict(start, conflict) for conflict in conflicts]


def pairs(*items: tuple[datetime, str]) -> SeriesOverride:
    return SeriesOverride(
        pairs=[{"occurrence_start": s, "code": c} for s, c in items],  # type: ignore[misc]
        reason=REASON,
    )


def three_sundays() -> list[OccurrenceConflict]:
    return [*at(FIRST, HOURS), *at(SECOND, HOURS), *at(THIRD, HOURS)]


# --- C1: the decision --------------------------------------------------------------


def test_no_conflict_is_allowed_with_nothing_overridden():
    assert evaluate_series_override([], None) == SeriesOverrideDecision(allowed=True)
    assert evaluate_series_override(
        [], pairs((FIRST, "outside_operating_hours"))
    ) == SeriesOverrideDecision(allowed=True)


def test_without_an_override_any_conflict_refuses():
    assert evaluate_series_override(three_sundays(), None) == SeriesOverrideDecision(
        allowed=False
    )


def test_every_soft_pair_listed_is_allowed_per_occurrence():
    decision = evaluate_series_override(
        three_sundays(),
        pairs(
            (FIRST, "outside_operating_hours"),
            (SECOND, "outside_operating_hours"),
            (THIRD, "outside_operating_hours"),
        ),
    )

    assert decision == SeriesOverrideDecision(
        allowed=True,
        occurrences=(
            OverriddenOccurrence(FIRST, (HOURS,)),
            OverriddenOccurrence(SECOND, (HOURS,)),
            OverriddenOccurrence(THIRD, (HOURS,)),
        ),
    )


def test_one_unlisted_occurrence_refuses_the_whole_write():
    decision = evaluate_series_override(
        three_sundays(),
        pairs((FIRST, "outside_operating_hours"), (SECOND, "outside_operating_hours")),
    )

    assert decision == SeriesOverrideDecision(allowed=False)


def test_an_unlisted_code_on_a_listed_occurrence_refuses():
    conflicts = [*at(FIRST, UNQUALIFIED, NOTICE), *at(SECOND, UNQUALIFIED)]

    refused = evaluate_series_override(
        conflicts,
        pairs((FIRST, "tutor_not_qualified"), (SECOND, "tutor_not_qualified")),
    )
    allowed = evaluate_series_override(
        conflicts,
        pairs(
            (FIRST, "tutor_not_qualified"),
            (FIRST, "notice_window"),
            (SECOND, "tutor_not_qualified"),
        ),
    )

    assert refused == SeriesOverrideDecision(allowed=False)
    assert allowed.allowed
    assert allowed.occurrences[0] == OverriddenOccurrence(FIRST, (UNQUALIFIED, NOTICE))


def test_a_hard_conflict_on_one_occurrence_refuses_everything():
    conflicts = [*three_sundays(), *at(SECOND, TUTOR)]

    decision = evaluate_series_override(
        conflicts,
        pairs(
            (FIRST, "outside_operating_hours"),
            (SECOND, "outside_operating_hours"),
            (THIRD, "outside_operating_hours"),
        ),
    )

    assert decision == SeriesOverrideDecision(allowed=False)


def test_extra_pairs_are_ignored_and_never_returned():
    decision = evaluate_series_override(
        at(FIRST, HOURS),
        pairs(
            (FIRST, "outside_operating_hours"),
            (FIRST, "center_closed"),
            (SECOND, "outside_operating_hours"),
            (FIRST + timedelta(minutes=15), "notice_window"),
        ),
    )

    assert decision == SeriesOverrideDecision(
        allowed=True, occurrences=(OverriddenOccurrence(FIRST, (HOURS,)),)
    )


def test_instants_are_compared_in_utc_across_offsets():
    # 10:00-06:00 is 16:00Z: the same instant, written with another offset.
    local = FIRST.astimezone(MX)
    assert local.isoformat() == "2031-01-12T10:00:00-06:00"

    decision = evaluate_series_override(
        at(local, HOURS), pairs((FIRST, "outside_operating_hours"))
    )
    other_way = evaluate_series_override(
        at(FIRST, HOURS), pairs((local, "outside_operating_hours"))
    )

    assert decision.allowed and other_way.allowed
    assert decision.occurrences[0].occurrence_start == FIRST
    assert decision.occurrences[0].occurrence_start.utcoffset() == timedelta(0)


def test_the_right_wall_time_with_the_wrong_offset_does_not_match():
    wrong = FIRST.replace(tzinfo=None).replace(tzinfo=MX)  # 16:00-06:00 = 22:00Z

    decision = evaluate_series_override(
        at(FIRST, HOURS), pairs((wrong, "outside_operating_hours"))
    )

    assert decision == SeriesOverrideDecision(allowed=False)


@pytest.mark.parametrize("code", sorted(SOFT))
def test_each_occurrence_follows_the_single_session_rule(code):
    conflict = Conflict(code, {})
    single = evaluate_override([conflict], SessionOverride(codes=[code], reason=REASON))

    series = evaluate_series_override(at(FIRST, conflict), pairs((FIRST, code)))

    assert single.allowed and series.allowed
    assert series.occurrences[0].conflicts == single.overridden


# --- B1-B3: the body ----------------------------------------------------------------


def codes_of(data: Any) -> list[tuple[tuple[Any, ...], str]]:
    with pytest.raises(ValidationError) as raised:
        SeriesOverride.model_validate(data)
    return [(tuple(e["loc"]), e["type"]) for e in raised.value.errors()]


def pair(start: str = "2031-01-12T16:00:00Z", code: str = "outside_operating_hours"):
    return {"occurrence_start": start, "code": code}


def test_the_reason_is_trimmed_and_checked_as_for_a_session():
    accepted = SeriesOverride.model_validate({"pairs": [pair()], "reason": "  Sí  "})

    assert accepted.reason == "Sí"
    assert codes_of({"pairs": [pair()]}) == [(("reason",), "reason_required")]
    assert codes_of({"pairs": [pair()], "reason": "   "}) == [
        (("reason",), "reason_required")
    ]
    assert codes_of({"pairs": [pair()], "reason": "a" * 1001}) == [
        (("reason",), "reason_too_long")
    ]
    assert SeriesOverride.model_validate({"pairs": [pair()], "reason": "a" * 1000})


def test_missing_or_empty_pairs_are_pairs_required():
    assert codes_of({"reason": REASON}) == [(("pairs",), "pairs_required")]
    assert codes_of({"pairs": [], "reason": REASON}) == [(("pairs",), "pairs_required")]


def test_1400_pairs_fit_and_1401_are_too_many():
    def many(n: int) -> list[dict[str, str]]:
        start = datetime(2031, 1, 12, 16, tzinfo=UTC)
        return [
            pair((start + timedelta(days=i // 7)).isoformat(), sorted(SOFT)[i % 7])
            for i in range(n)
        ]

    assert len(SeriesOverride(pairs=many(1400), reason=REASON).pairs) == 1400  # type: ignore[arg-type]
    assert codes_of({"pairs": many(1401), "reason": REASON}) == [
        (("pairs",), "pairs_too_many")
    ]


def test_a_duplicate_pair_is_found_after_normalizing_to_utc():
    assert codes_of(
        {
            "pairs": [pair(), pair("2031-01-12T10:00:00-06:00")],
            "reason": REASON,
        }
    ) == [(("pairs",), "duplicate_pair")]
    # The same instant with another code is not a duplicate.
    assert SeriesOverride.model_validate(
        {"pairs": [pair(), pair(code="notice_window")], "reason": REASON}
    )


def test_hard_warning_and_unknown_codes_are_refused_on_their_item():
    assert codes_of(
        {
            "pairs": [
                pair(),
                pair(code="tutor_overlap"),
                pair(code="daily_limit_exceeded"),
                pair(code="nope"),
            ],
            "reason": REASON,
        }
    ) == [
        (("pairs", 1, "code"), "code_not_overridable"),
        (("pairs", 2, "code"), "code_unknown"),
        (("pairs", 3, "code"), "code_unknown"),
    ]


@pytest.mark.parametrize("code", sorted(HARD))
def test_every_hard_code_is_not_overridable(code):
    assert codes_of({"pairs": [pair(code=code)], "reason": REASON}) == [
        (("pairs", 0, "code"), "code_not_overridable")
    ]


@pytest.mark.parametrize("code", sorted(WARNING))
def test_every_warning_code_is_unknown(code):
    assert codes_of({"pairs": [pair(code=code)], "reason": REASON}) == [
        (("pairs", 0, "code"), "code_unknown")
    ]


@pytest.mark.parametrize(
    "start",
    ["2031-01-12T16:00:00", "2031-01-12T16:00:30Z", "2031-01-12T16:00:00.5Z"],
)
def test_a_naive_or_not_whole_minute_instant_is_invalid(start):
    assert codes_of({"pairs": [pair(start)], "reason": REASON}) == [
        (("pairs", 0, "occurrence_start"), "occurrence_start_invalid")
    ]


def test_codes_and_unknown_members_are_refused():
    assert codes_of(
        {"pairs": [pair()], "codes": ["notice_window"], "reason": REASON}
    ) == [(("codes",), "extra_forbidden")]
    assert codes_of({"pairs": [pair() | {"x": 1}], "reason": REASON}) == [
        (("pairs", 0, "x"), "extra_forbidden")
    ]


def test_the_series_bodies_take_an_optional_pairs_override():
    create = {
        "kind": "individual",
        "student_id": "00000000-0000-4000-8000-000000000001",
        "subject_id": "00000000-0000-4000-8000-000000000002",
        "starts_on": "2031-01-12",
        "start_time": "10:00",
        "duration_minutes": 60,
        "recurrence": {"weekdays": [7], "count": 3},
    }
    edit = {
        "mode": "all",
        "from_date": None,
        "version": 1,
        "start_time": "10:00",
        "duration_minutes": 60,
        "classroom_id": None,
        "recurrence": {"weekdays": [7]},
    }
    body = {"pairs": [pair()], "reason": REASON}

    assert ClassSeriesCreate.model_validate(create).override is None
    assert ClassSeriesEdit.model_validate(edit).override is None
    assert ClassSeriesCreate.model_validate(create | {"override": body}).override
    assert ClassSeriesEdit.model_validate(edit | {"override": body}).override
    with pytest.raises(ValidationError):
        ClassSeriesCreate.model_validate(
            create | {"override": {"codes": ["notice_window"], "reason": REASON}}
        )


def test_the_conflict_override_body_takes_the_session_member():
    body = ConflictOverrideCreate.model_validate(
        {"version": 3, "override": {"codes": ["notice_window"], "reason": REASON}}
    )

    assert body.override.codes == ["notice_window"]
    with pytest.raises(ValidationError) as raised:
        ConflictOverrideCreate.model_validate({"version": 3})
    assert [e["type"] for e in raised.value.errors()] == ["missing"]
    with pytest.raises(ValidationError):
        ConflictOverrideCreate.model_validate(
            {"version": 3, "override": {"pairs": [pair()], "reason": REASON}}
        )
