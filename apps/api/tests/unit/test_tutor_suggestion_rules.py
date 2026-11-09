"""The HF-05 rules without a database: the series overlap and availability
checks (C4), the count buckets (B1) and the new bodies (A1, C1)."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.modules.scheduling.eligibility import (
    series_is_available,
    series_is_busy,
    suggestion_counts,
)
from app.modules.scheduling.recurrence import (
    RecurrenceFields,
    build_rrule,
    expand,
)
from app.modules.scheduling.schemas import SeriesTutorSuggestionQuery
from app.modules.staff.availability import (
    DatedException,
    TutorAvailabilityRules,
    WeeklyWindow,
)
from app.modules.staff.schemas import QualificationGrant

pytestmark = pytest.mark.unit

NEW_YORK = "America/New_York"
NY = ZoneInfo(NEW_YORK)
# DST starts on Sunday 2031-03-09 in New York: the first Monday is UTC-5,
# the next two UTC-4.
FIRST_MONDAY = date(2031, 3, 3)


def mondays_at_four(weeks: int = 3) -> list[tuple[datetime, datetime]]:
    """A weekly Monday 16:00-17:00 rule in New York, expanded as a series."""
    fields = RecurrenceFields(weekdays=(1,), interval=1, until_on=None, count=None)
    start = datetime.combine(FIRST_MONDAY, time(), tzinfo=NY)
    occurrences = expand(
        build_rrule(fields),
        starts_on=FIRST_MONDAY,
        start_time=time(16),
        duration_minutes=60,
        zone=NEW_YORK,
        window_start=start,
        window_end=start + timedelta(weeks=weeks),
    )
    return [(o.starts_at, o.ends_at) for o in occurrences]


def local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=NY).astimezone(UTC)


def test_the_occurrences_keep_their_wall_clock_across_dst():
    starts = [start for start, _ in mondays_at_four()]

    assert [s.hour for s in starts] == [21, 20, 20]  # 16:00 EST, then EDT


# --- series_is_busy ------------------------------------------------------------------


def test_no_sessions_is_free():
    assert not series_is_busy(mondays_at_four(), [])


def test_back_to_back_sessions_are_free():
    second = FIRST_MONDAY + timedelta(weeks=1)
    sessions = [
        (local(second, 15), local(second, 16)),
        (local(second, 17), local(second, 18)),
    ]

    assert not series_is_busy(mondays_at_four(), sessions)


def test_one_conflicting_occurrence_makes_the_tutor_busy():
    third = FIRST_MONDAY + timedelta(weeks=2)
    sessions = [(local(third, 16, 30), local(third, 17, 30))]

    assert series_is_busy(mondays_at_four(), sessions)


def test_a_long_session_covering_an_occurrence_is_found():
    # A session that starts before the occurrence and ends after it: the
    # running maximum of ends catches it behind a later short one.
    second = FIRST_MONDAY + timedelta(weeks=1)
    sessions = [
        (local(second, 8), local(second, 20)),
        (local(second, 9), local(second, 10)),
    ]

    assert series_is_busy(mondays_at_four(), sessions)


def test_the_same_utc_hour_after_dst_is_not_the_occurrence():
    # 21:00 UTC is 16:00 before the change and 17:00 after it.
    second = FIRST_MONDAY + timedelta(weeks=1)
    at_21_utc = datetime.combine(second, time(21), tzinfo=UTC)

    assert not series_is_busy(
        mondays_at_four(), [(at_21_utc, at_21_utc + timedelta(hours=1))]
    )


# --- series_is_available -------------------------------------------------------------

MONDAY_AFTERNOON = (WeeklyWindow(weekday=1, starts_at=time(15), ends_at=time(18)),)


def test_a_weekly_window_covers_every_occurrence_across_dst():
    rules = TutorAvailabilityRules(windows=MONDAY_AFTERNOON, exceptions=())

    assert series_is_available(rules, mondays_at_four(), NEW_YORK)


def test_one_day_off_drops_the_tutor():
    third = FIRST_MONDAY + timedelta(weeks=2)
    rules = TutorAvailabilityRules(
        windows=MONDAY_AFTERNOON,
        exceptions=(
            DatedException(kind="unavailable", starts_on=third, ends_on=third),
        ),
    )

    assert not series_is_available(rules, mondays_at_four(), NEW_YORK)


def test_a_window_too_short_on_one_side_of_dst_drops_the_tutor():
    rules = TutorAvailabilityRules(
        windows=(WeeklyWindow(weekday=1, starts_at=time(16), ends_at=time(16, 30)),),
        exceptions=(),
    )

    assert not series_is_available(rules, mondays_at_four(), NEW_YORK)


def test_no_occurrence_is_trivially_available():
    rules = TutorAvailabilityRules(windows=(), exceptions=())

    assert series_is_available(rules, [], NEW_YORK)


# --- suggestion_counts ---------------------------------------------------------------


def ids(n: int) -> list[uuid.UUID]:
    return [uuid.uuid4() for _ in range(n)]


def test_the_counts_add_up():
    qualified = ids(6)
    busy = qualified[:2]
    eligible = qualified[2:3]

    counts = suggestion_counts(qualified, busy, eligible)

    assert (counts.qualified, counts.eligible, counts.busy, counts.unavailable) == (
        6,
        1,
        2,
        3,
    )
    assert counts.qualified == counts.eligible + counts.busy + counts.unavailable


def test_busy_wins_over_eligible_and_outsiders_are_ignored():
    qualified = ids(3)
    outsider = uuid.uuid4()

    counts = suggestion_counts(qualified, [qualified[0], outsider], qualified[:2])

    assert (counts.qualified, counts.eligible, counts.busy, counts.unavailable) == (
        3,
        1,
        1,
        1,
    )


def test_nobody_qualified_is_all_zero():
    counts = suggestion_counts([], [], [])

    assert (counts.qualified, counts.eligible, counts.busy, counts.unavailable) == (
        0,
        0,
        0,
        0,
    )


# --- The bodies ----------------------------------------------------------------------


def test_the_grant_body_takes_only_the_subject():
    subject = uuid.uuid4()

    assert QualificationGrant.model_validate(
        {"subject_id": str(subject)}
    ).subject_id == (subject)
    with pytest.raises(ValidationError):
        QualificationGrant.model_validate({"subject_id": str(subject), "note": "x"})
    with pytest.raises(ValidationError):
        QualificationGrant.model_validate({})


def series_query(**changes: object) -> dict[str, object]:
    return {
        "subject_id": str(uuid.uuid4()),
        "starts_on": "2031-03-03",
        "start_time": "16:00",
        "duration_minutes": 60,
        "recurrence": {"weekdays": [1]},
    } | changes


def test_the_series_query_defaults():
    query = SeriesTutorSuggestionQuery.model_validate(series_query())

    assert (query.limit, query.classroom_id, query.recurrence.interval) == (10, None, 1)


@pytest.mark.parametrize(
    "changes",
    [
        {"tutor_user_id": str(uuid.uuid4())},
        {"start_time": "16:00:30"},
        {"duration_minutes": 10},
        {"duration_minutes": 481},
        {"limit": 0},
        {"limit": 51},
        {"recurrence": {"weekdays": [1], "rrule": "FREQ=WEEKLY"}},
    ],
    ids=["unknown", "seconds", "short", "long", "limit-0", "limit-51", "rrule"],
)
def test_the_series_query_refuses(changes):
    with pytest.raises(ValidationError):
        SeriesTutorSuggestionQuery.model_validate(series_query(**changes))
