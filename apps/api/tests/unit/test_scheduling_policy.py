"""The scheduling policy evaluator (#61 B5): every boundary of
``classify_change``, the makeup rule, ``violates_booking_notice`` and
``default_respond_by``. Pure functions: no database, no clock."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.modules.administration.scheduling_policy import (
    SchedulingPolicyValues,
    classify_change,
    default_respond_by,
    violates_booking_notice,
)

POLICY = SchedulingPolicyValues(
    id=uuid.UUID("01900000-0000-7000-8000-000000000001"),
    effective_from=datetime(2000, 1, 1, tzinfo=UTC),
    booking_notice_hours=24,
    change_notice_hours=24,
    response_hours=48,
    makeup_after_on_time=True,
    makeup_after_late=False,
    makeup_after_no_show=False,
    makeup_window_days=30,
)
STARTS = datetime(2026, 11, 10, 16, 0, tzinfo=UTC)
ENDS = STARTS + timedelta(hours=1)
H = timedelta(hours=1)
M = timedelta(minutes=1)

# (request time, classification for a family cancellation)
FAMILY_CANCELLATIONS = [
    (STARTS - 24 * H - M, "on_time"),
    (STARTS - 24 * H, "on_time"),
    (STARTS - 23 * H - 59 * M, "late"),
    (STARTS - M, "late"),
    (STARTS, "no_show"),
    (ENDS + M, "no_show"),
]


def classify(
    at: datetime,
    *,
    kind: str = "cancellation",
    initiated_by: str = "family",
    policy: SchedulingPolicyValues = POLICY,
    starts_at: datetime = STARTS,
):
    return classify_change(
        policy,
        kind=kind,  # type: ignore[arg-type]
        initiated_by=initiated_by,  # type: ignore[arg-type]
        starts_at=starts_at,
        at=at,
    )


# --- classification ---------------------------------------------------------------


@pytest.mark.parametrize(("at", "expected"), FAMILY_CANCELLATIONS)
def test_a_family_cancellation_is_classified_by_the_notice(at, expected):
    outcome = classify(at)

    assert outcome.classification == expected
    assert outcome.policy_id == POLICY.id


@pytest.mark.parametrize(("at", "expected"), FAMILY_CANCELLATIONS)
def test_a_family_reschedule_is_classified_like_a_cancellation(at, expected):
    assert classify(at, kind="reschedule").classification == expected


@pytest.mark.parametrize("at", [STARTS - 48 * H, STARTS - M, STARTS, ENDS + M])
def test_an_absence_is_a_no_show(at):
    assert classify(at, kind="absence").classification == "no_show"


@pytest.mark.parametrize("initiated_by", ["tutor", "center"])
@pytest.mark.parametrize("kind", ["cancellation", "reschedule", "absence"])
@pytest.mark.parametrize(("at", "_family"), FAMILY_CANCELLATIONS)
def test_a_tutor_or_center_change_is_always_excused(initiated_by, kind, at, _family):
    assert classify(at, kind=kind, initiated_by=initiated_by).classification == (
        "excused"
    )


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (STARTS - 24 * H, "on_time"),
        (STARTS - M, "on_time"),
        (STARTS - timedelta(microseconds=1), "on_time"),
        (STARTS, "no_show"),
    ],
)
def test_with_no_change_notice_nothing_before_the_start_is_late(at, expected):
    policy = replace(POLICY, change_notice_hours=0)

    assert classify(at, policy=policy).classification == expected


def test_another_notice_moves_the_boundary():
    policy = replace(POLICY, change_notice_hours=12)

    assert classify(STARTS - 12 * H, policy=policy).classification == "on_time"
    assert classify(STARTS - 12 * H + M, policy=policy).classification == "late"


# --- makeups ----------------------------------------------------------------------

ON_TIME_AT = STARTS - 30 * H
LATE_AT = STARTS - H
NO_SHOW_AT = STARTS + M


@pytest.mark.parametrize("flag", [True, False])
@pytest.mark.parametrize(
    ("at", "field", "classification"),
    [
        (ON_TIME_AT, "makeup_after_on_time", "on_time"),
        (LATE_AT, "makeup_after_late", "late"),
        (NO_SHOW_AT, "makeup_after_no_show", "no_show"),
    ],
)
def test_each_flag_decides_its_classification(at, field, classification, flag):
    others = {
        name: not flag
        for name in (
            "makeup_after_on_time",
            "makeup_after_late",
            "makeup_after_no_show",
        )
        if name != field
    }
    policy = replace(POLICY, **{field: flag}, **others)

    outcome = classify(at, policy=policy)

    assert outcome.classification == classification
    assert outcome.makeup_allowed is flag
    assert outcome.makeup_until == (STARTS + timedelta(days=30) if flag else None)


@pytest.mark.parametrize("flag", [True, False])
def test_a_recorded_absence_follows_the_no_show_flag(flag):
    policy = replace(POLICY, makeup_after_no_show=flag)

    assert classify(STARTS - H, kind="absence", policy=policy).makeup_allowed is flag


@pytest.mark.parametrize("initiated_by", ["tutor", "center"])
@pytest.mark.parametrize("kind", ["cancellation", "absence"])
@pytest.mark.parametrize("at", [ON_TIME_AT, LATE_AT, NO_SHOW_AT])
def test_an_excused_change_always_allows_a_makeup(initiated_by, kind, at):
    policy = replace(
        POLICY,
        makeup_after_on_time=False,
        makeup_after_late=False,
        makeup_after_no_show=False,
    )

    outcome = classify(at, kind=kind, initiated_by=initiated_by, policy=policy)

    assert outcome.classification == "excused"
    assert outcome.makeup_allowed is True
    assert outcome.makeup_until == STARTS + timedelta(days=30)


@pytest.mark.parametrize("initiated_by", ["family", "tutor", "center"])
@pytest.mark.parametrize("at", [ON_TIME_AT, LATE_AT, NO_SHOW_AT])
def test_a_reschedule_never_allows_a_makeup(initiated_by, at):
    policy = replace(
        POLICY,
        makeup_after_on_time=True,
        makeup_after_late=True,
        makeup_after_no_show=True,
    )

    outcome = classify(at, kind="reschedule", initiated_by=initiated_by, policy=policy)

    assert outcome.makeup_allowed is False
    assert outcome.makeup_until is None


def test_makeup_until_counts_days_of_24_hours():
    policy = replace(POLICY, makeup_window_days=1)

    outcome = classify(ON_TIME_AT, policy=policy)

    assert outcome.makeup_until == STARTS + 24 * H


# --- booking notice ---------------------------------------------------------------

BOOKED_AT = datetime(2026, 11, 9, 16, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("starts_at", "expected"),
    [
        (BOOKED_AT + 24 * H, False),
        (BOOKED_AT + 24 * H - M, True),
        (BOOKED_AT + 24 * H + M, False),
    ],
)
def test_the_booking_notice_boundary(starts_at, expected):
    assert violates_booking_notice(POLICY, starts_at=starts_at, at=BOOKED_AT) is (
        expected
    )


@pytest.mark.parametrize(
    ("starts_at", "expected"),
    [
        (BOOKED_AT + M, False),
        (BOOKED_AT, False),
        (BOOKED_AT - M, True),
    ],
)
def test_with_no_booking_notice_only_a_past_start_violates(starts_at, expected):
    policy = replace(POLICY, booking_notice_hours=0)

    assert violates_booking_notice(policy, starts_at=starts_at, at=BOOKED_AT) is (
        expected
    )


# --- offer deadline ---------------------------------------------------------------


def test_the_response_time_comes_first():
    first = BOOKED_AT + 72 * H

    assert (
        default_respond_by(POLICY, offered_at=BOOKED_AT, first_starts_at=first)
        == BOOKED_AT + 48 * H
    )


def test_the_first_start_comes_first():
    first = BOOKED_AT + 10 * H

    assert (
        default_respond_by(POLICY, offered_at=BOOKED_AT, first_starts_at=first) == first
    )


# --- instants ---------------------------------------------------------------------

NAIVE = datetime(2026, 11, 10, 16, 0)


@pytest.mark.parametrize(
    "call",
    [
        lambda: classify(NAIVE),
        lambda: classify(BOOKED_AT, starts_at=NAIVE),
        lambda: violates_booking_notice(POLICY, starts_at=NAIVE, at=BOOKED_AT),
        lambda: violates_booking_notice(POLICY, starts_at=STARTS, at=NAIVE),
        lambda: default_respond_by(POLICY, offered_at=NAIVE, first_starts_at=STARTS),
        lambda: default_respond_by(POLICY, offered_at=BOOKED_AT, first_starts_at=NAIVE),
    ],
)
def test_naive_datetimes_raise(call):
    with pytest.raises(ValueError, match="aware"):
        call()


CANCUN = timezone(timedelta(hours=-5))
TOKYO = timezone(timedelta(hours=9))


@pytest.mark.parametrize(("at", "expected"), FAMILY_CANCELLATIONS)
def test_two_offsets_of_the_same_instants_give_the_same_answer(at, expected):
    outcome = classify(at.astimezone(CANCUN), starts_at=STARTS.astimezone(TOKYO))

    assert outcome.classification == expected
    assert outcome == classify(at)


def test_booking_and_deadline_compare_instants_too():
    at = BOOKED_AT.astimezone(CANCUN)
    starts = (BOOKED_AT + 24 * H - M).astimezone(TOKYO)

    assert violates_booking_notice(POLICY, starts_at=starts, at=at) is True
    assert (
        default_respond_by(
            POLICY,
            offered_at=at,
            first_starts_at=(BOOKED_AT + 72 * H).astimezone(TOKYO),
        )
        == BOOKED_AT + 48 * H
    )
