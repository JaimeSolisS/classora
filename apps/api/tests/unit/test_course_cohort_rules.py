"""Cohort rules (#65 B1-B4), pure: the derived ``COUNT``, the status and
the syllabus of a snapshot; and the course rule's first 200 dates."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.modules.courses.cohorts import (
    TooManyOccurrencesError,
    cohort_status,
    derive_occurrence_count,
    snapshot_subject_ids,
    syllabus_of,
)
from app.modules.courses.schemas import (
    CourseTemplateAssessment,
    CourseTemplateModule,
)
from app.modules.courses.templates import (
    SnapshotSubject,
    TemplateContent,
    build_snapshot,
)
from app.modules.scheduling.course_series import course_rule_dates

MATH = uuid.UUID("01900000-0000-7000-8000-000000000001")
LEVEL = uuid.UUID("01900000-0000-7000-8000-0000000000a1")
M1 = uuid.UUID("01900000-0000-7000-8000-000000000101")
M2 = uuid.UUID("01900000-0000-7000-8000-000000000102")
M3 = uuid.UUID("01900000-0000-7000-8000-000000000103")
A1 = uuid.UUID("01900000-0000-7000-8000-000000000201")
A2 = uuid.UUID("01900000-0000-7000-8000-000000000202")

MONDAY = date(2031, 1, 6)


def mondays_and_wednesdays(n: int) -> list[date]:
    """The first ``n`` Mondays and Wednesdays from :data:`MONDAY`."""
    found: list[date] = []
    day = MONDAY
    while len(found) < n:
        if day.isoweekday() in (1, 3):
            found.append(day)
        day += timedelta(days=1)
    return found


# --- B1: the derived COUNT -----------------------------------------------------------


def test_without_closures_the_count_is_the_session_count():
    assert derive_occurrence_count(mondays_and_wednesdays(200), set(), 10) == 10


def test_two_closures_inside_the_span_add_two_occurrences():
    dates = mondays_and_wednesdays(200)
    closed = {dates[2], dates[5]}

    assert derive_occurrence_count(dates, closed, 10) == 12


def test_a_closure_after_the_last_session_is_not_counted():
    dates = mondays_and_wednesdays(200)

    assert derive_occurrence_count(dates, {dates[10], dates[11]}, 10) == 10


def test_a_closure_on_the_first_date_moves_the_end_by_one():
    dates = mondays_and_wednesdays(200)

    assert derive_occurrence_count(dates, {dates[0]}, 10) == 11


def test_two_hundred_sessions_without_closures_fit():
    assert derive_occurrence_count(mondays_and_wednesdays(200), set(), 200) == 200


def test_two_hundred_sessions_and_one_closure_are_too_many():
    dates = mondays_and_wednesdays(200)

    with pytest.raises(TooManyOccurrencesError) as caught:
        derive_occurrence_count(dates, {dates[50]}, 200)

    assert (caught.value.field, caught.value.reason) == (
        "recurrence",
        "too_many_occurrences",
    )


def test_dates_past_the_200th_are_never_used():
    dates = mondays_and_wednesdays(210)

    with pytest.raises(TooManyOccurrencesError):
        derive_occurrence_count(dates, {dates[0]}, 200)


def test_a_session_count_below_one_is_a_programming_error():
    with pytest.raises(ValueError, match="at least 1"):
        derive_occurrence_count(mondays_and_wednesdays(5), set(), 0)


# --- The course rule's dates (D2 step 7) ---------------------------------------------


def test_the_rule_dates_are_the_first_200_occurrences():
    dates = course_rule_dates(starts_on=MONDAY, weekdays=[1, 3], interval=1)

    assert dates == mondays_and_wednesdays(200)


def test_the_rule_dates_follow_the_interval_and_a_start_off_the_weekdays():
    dates = course_rule_dates(starts_on=date(2031, 1, 5), weekdays=[2], interval=2)

    assert len(dates) == 200
    assert dates[:3] == [date(2031, 1, 14), date(2031, 1, 28), date(2031, 2, 11)]


# --- B2: the status ------------------------------------------------------------------

FIRST = datetime(2031, 1, 6, 17, tzinfo=UTC)
LAST = datetime(2031, 3, 10, 17, tzinfo=UTC)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (FIRST - timedelta(minutes=1), "upcoming"),
        (FIRST, "in_progress"),
        (LAST, "in_progress"),
        (LAST + timedelta(minutes=89), "in_progress"),
        (LAST + timedelta(minutes=90), "ended"),
        (LAST + timedelta(days=30), "ended"),
    ],
)
def test_the_status_at_each_boundary(now, expected):
    assert cohort_status(FIRST, LAST, now, session_minutes=90) == expected


# --- B3: the syllabus ----------------------------------------------------------------


def snapshot_content() -> dict:
    """A #64 snapshot (schema version 1) whose items are out of order."""
    return build_snapshot(
        TemplateContent(
            name="SAT",
            description=None,
            session_count=10,
            session_minutes=90,
            min_attendance_percent=80,
            subjects=[
                SnapshotSubject(
                    id=MATH, name="Mate", level_id=LEVEL, level_name="Prepa"
                )
            ],
            modules=[
                CourseTemplateModule(
                    id=M3,
                    position=3,
                    title="Cierre",
                    description=None,
                    subject_id=None,
                    planned_sessions=None,
                    is_required=False,
                    materials=None,
                ),
                CourseTemplateModule(
                    id=M1,
                    position=1,
                    title="Intro",
                    description="Bases",
                    subject_id=MATH,
                    planned_sessions=2,
                    is_required=True,
                    materials="Libro",
                ),
                CourseTemplateModule(
                    id=M2,
                    position=2,
                    title="Desarrollo",
                    description=None,
                    subject_id=None,
                    planned_sessions=5,
                    is_required=True,
                    materials=None,
                ),
            ],
            assessments=[
                CourseTemplateAssessment(
                    id=A2,
                    position=2,
                    title="Final",
                    module_id=None,
                    is_required=True,
                    min_score_percent=None,
                ),
                CourseTemplateAssessment(
                    id=A1,
                    position=1,
                    title="Parcial",
                    module_id=M2,
                    is_required=False,
                    min_score_percent=60,
                ),
            ],
        )
    )


def test_the_syllabus_follows_the_snapshot_in_position_order():
    syllabus = syllabus_of(snapshot_content())

    assert [(m.id, m.position, m.title) for m in syllabus.modules] == [
        (M1, 1, "Intro"),
        (M2, 2, "Desarrollo"),
        (M3, 3, "Cierre"),
    ]
    first = syllabus.modules[0]
    assert (first.description, first.subject_id, first.planned_sessions) == (
        "Bases",
        MATH,
        2,
    )
    assert (first.is_required, first.materials) == (True, "Libro")
    assert [(a.id, a.module_id, a.min_score_percent) for a in syllabus.assessments] == [
        (A1, M2, 60),
        (A2, None, None),
    ]
    assert syllabus.completion.min_attendance_percent == 80
    assert syllabus.completion.required_module_ids == [M1, M2]
    assert syllabus.completion.required_assessment_ids == [A2]


def test_an_unknown_schema_version_is_refused():
    with pytest.raises(ValueError, match="schema version 2"):
        syllabus_of(snapshot_content(), schema_version=2)


def test_the_snapshot_subjects_are_read_in_order():
    assert snapshot_subject_ids(snapshot_content()) == (MATH,)
