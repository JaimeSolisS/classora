"""The progress dashboard's pure rules (#84 A3, B1, B2, C2, E6, AM1;
``academics.progress_dashboard_rules`` and the pure helpers of
``academics.progress_aggregates``) and the closed view allow-lists."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import product
from typing import Any

import pytest
from pydantic import BaseModel

from app.modules.academics import progress_dashboard_rules as rules
from app.modules.academics.learning_plans import MilestoneFact
from app.modules.academics.progress_aggregates import (
    HomeworkRow,
    is_counted_grade,
    plan_counts,
)
from app.workflows import progress_dashboard_schemas as views

Audience = rules.Audience
FAMILY_VALUES: tuple[rules.FamilyAccess, ...] = ("allowed", "consent_required", "none")


# --- A3: the audience and the matrix --------------------------------------------------


@pytest.mark.parametrize(
    ("staff", "current", "family", "former", "expected"),
    [
        # Each matrix row.
        (True, False, "none", False, Audience.STAFF),
        (False, True, "none", False, Audience.CURRENT_TUTOR),
        (False, False, "none", True, Audience.FORMER_TUTOR),
        (False, False, "none", False, Audience.NONE),
        (False, False, "allowed", False, Audience.FAMILY),
        (False, False, "consent_required", False, Audience.NONE),
        # Role unions: the widest wins.
        (True, True, "allowed", True, Audience.STAFF),
        (False, True, "consent_required", True, Audience.CURRENT_TUTOR),
        (False, False, "allowed", True, Audience.FAMILY),
        (False, False, "consent_required", True, Audience.FORMER_TUTOR),
    ],
)
def test_the_audience(staff, current, family, former, expected):
    assert (
        rules.progress_audience(
            staff=staff, current_tutor=current, family=family, former_tutor=former
        )
        is expected
    )


@pytest.mark.parametrize(
    ("staff", "current", "family", "former"),
    list(product((True, False), (True, False), FAMILY_VALUES, (True, False))),
)
def test_every_combination_is_decided(staff, current, family, former):
    audience = rules.progress_audience(
        staff=staff, current_tutor=current, family=family, former_tutor=former
    )
    progress = rules.route_block(audience, family, "progress")
    log = rules.route_block(audience, family, "session_log")
    full = staff or current or family == "allowed"
    assert (progress is None) is full
    assert (log is None) is (full or former)
    if not full and family == "consent_required":
        assert progress == rules.CONSENT_REQUIRED
    if not full and family == "none":
        assert progress == rules.NOT_FOUND
    if not (full or former):
        expected = (
            rules.CONSENT_REQUIRED if family == "consent_required" else rules.NOT_FOUND
        )
        assert log == expected


@pytest.mark.parametrize(
    ("audience", "family", "progress", "log"),
    [
        (Audience.STAFF, "none", None, None),
        (Audience.CURRENT_TUTOR, "none", None, None),
        (Audience.FAMILY, "allowed", None, None),
        (Audience.FORMER_TUTOR, "none", "not_found", None),
        (Audience.FORMER_TUTOR, "consent_required", "student_consent_required", None),
        (
            Audience.NONE,
            "consent_required",
            "student_consent_required",
            "student_consent_required",
        ),
        (Audience.NONE, "none", "not_found", "not_found"),
    ],
)
def test_the_route_answers(audience, family, progress, log):
    assert rules.route_block(audience, family, "progress") == progress
    assert rules.route_block(audience, family, "session_log") == log


# --- E6: numbers ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [
        (1, 3, Decimal("33.33")),
        (2, 3, Decimal("66.67")),
        (1, 8, Decimal("12.50")),
        (1, 200, Decimal("0.50")),
        (1, 400, Decimal("0.25")),
        (1, 800, Decimal("0.13")),  # 0.125 half-up
        (3, 3, Decimal("100.00")),
        (0, 5, Decimal("0.00")),
        (0, 0, None),
    ],
)
def test_percent_is_a_two_place_half_up_decimal(numerator, denominator, expected):
    found = rules.percent(numerator, denominator)
    assert found == expected
    if found is not None:
        assert isinstance(found, Decimal)
        assert found.as_tuple().exponent == -2


def test_attendance_and_completion_percent():
    # Late counts as attended; unrecorded sessions aren't in the rate.
    assert rules.attendance_percent(1, 1, 1) == Decimal("66.67")
    assert rules.attendance_percent(0, 0, 0) is None
    assert rules.attendance_percent(0, 0, 2) == Decimal("0.00")
    # Pending work isn't counted yet.
    assert rules.completion_percent(1, 1, 1) == Decimal("66.67")
    assert rules.completion_percent(0, 0, 0) is None
    assert rules.completion_percent(0, 2, 0) == Decimal("100.00")


# --- E6: months -----------------------------------------------------------------------


def test_the_months():
    assert rules.month_key(date(2031, 3, 5)) == "2031-03"
    assert rules.months_between(date(2030, 11, 30), date(2031, 2, 1)) == [
        "2030-11",
        "2030-12",
        "2031-01",
        "2031-02",
    ]
    assert rules.months_between(date(2031, 3, 1), date(2031, 3, 31)) == ["2031-03"]
    assert rules.months_between(date(2031, 4, 1), date(2031, 3, 1)) == []
    assert rules.month_span(date(2029, 4, 1), date(2031, 3, 5)) == 24
    assert rules.month_span(date(2029, 3, 31), date(2031, 3, 5)) == 25
    assert rules.first_of_month_before(date(2031, 3, 31), 11) == date(2030, 4, 1)
    assert rules.first_of_month_before(date(2031, 1, 15), 1) == date(2030, 12, 1)


# --- B1: the range --------------------------------------------------------------------


def test_the_default_range_is_twelve_calendar_months():
    today = date(2031, 3, 5)
    assert rules.default_range(today) == (date(2030, 4, 1), today)
    assert rules.default_range(today, to=date(2031, 1, 31)) == (
        date(2030, 2, 1),
        date(2031, 1, 31),
    )
    assert rules.default_range(today, from_=date(2031, 3, 1)) == (
        date(2031, 3, 1),
        today,
    )
    start, end = rules.default_range(date(2031, 12, 31))
    assert (start, rules.month_span(start, end)) == (date(2031, 1, 1), 12)


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2031, 3, 1), date(2031, 3, 5), None),
        (date(2031, 3, 5), date(2031, 3, 5), None),
        (date(2029, 4, 1), date(2031, 3, 5), None),  # 24 months
        (date(2031, 3, 6), date(2031, 3, 5), ("from", "range_invalid")),
        (date(1999, 12, 31), date(2000, 1, 5), ("from", "range_invalid")),
        (date(2000, 1, 1), date(2000, 1, 5), None),
        (date(2031, 3, 1), date(2031, 3, 6), ("to", "range_in_future")),
        (date(2029, 3, 31), date(2031, 3, 5), ("from", "range_too_long")),
        # The first rule that applies wins.
        (date(1999, 1, 1), date(2031, 3, 6), ("from", "range_invalid")),
        (date(2020, 1, 1), date(2031, 3, 6), ("to", "range_in_future")),
    ],
)
def test_the_progress_range(start, end, expected):
    issue = rules.progress_range_issue(start, end, date(2031, 3, 5))
    assert (None if issue is None else issue[:2]) == expected


def test_the_log_range():
    assert rules.log_range_issue(None, None) is None
    assert rules.log_range_issue(date(2031, 3, 5), None) is None
    assert rules.log_range_issue(None, date(2000, 1, 1)) is None
    assert rules.log_range_issue(date(2031, 3, 5), date(2031, 3, 5)) is None
    issue = rules.log_range_issue(date(2031, 3, 6), date(2031, 3, 5))
    assert issue is not None and issue[:2] == ("from", "range_invalid")


# --- E6: the aggregate helpers --------------------------------------------------------


def _row(**values: Any) -> HomeworkRow:
    base: dict[str, Any] = {
        "subject_id": uuid.uuid4(),
        "subject_name": "Matemáticas",
        "due_on": date(2031, 3, 1),
        "due_time": None,
        "submitted_at": datetime(2031, 3, 1, tzinfo=UTC),
        "graded_at": datetime(2031, 3, 2, tzinfo=UTC),
        "graded_on": date(2031, 3, 2),
        "score_percent": Decimal("80.00"),
    }
    base.update(values)
    return HomeworkRow(**base)


def test_a_counted_grade_is_scored_and_graded_in_the_range():
    start, end = date(2031, 3, 1), date(2031, 3, 31)
    assert is_counted_grade(_row(), start, end)
    assert is_counted_grade(_row(graded_on=start), start, end)
    assert is_counted_grade(_row(graded_on=end, due_on=date(2031, 4, 9)), start, end)
    assert not is_counted_grade(_row(score_percent=None), start, end)  # feedback only
    assert not is_counted_grade(
        _row(graded_at=None, graded_on=None, score_percent=None), start, end
    )
    assert not is_counted_grade(_row(graded_on=date(2031, 4, 1)), start, end)
    assert not is_counted_grade(_row(graded_on=date(2031, 2, 28)), start, end)


def test_the_plan_counts():
    one, two = uuid.uuid4(), uuid.uuid4()
    plan = uuid.uuid4()
    today = date(2031, 3, 5)
    facts = [
        MilestoneFact(plan, one, "done", date(2031, 3, 1)),
        MilestoneFact(plan, one, "done", None),
        MilestoneFact(plan, two, "pending", date(2031, 3, 4)),  # overdue
        MilestoneFact(plan, two, "in_progress", today),  # due today: not overdue
        MilestoneFact(plan, two, "done", date(2031, 1, 1)),  # done: never overdue
    ]
    counts = plan_counts(facts, today)
    assert counts.progress_percent == Decimal(60)
    assert (counts.milestones_done, counts.milestones_total) == (3, 5)
    assert (counts.objectives_complete, counts.objectives_total) == (1, 2)
    assert counts.milestones_overdue == 1
    empty = plan_counts([], today)
    assert (empty.progress_percent, empty.objectives_total) == (Decimal(0), 0)


# --- B2, C2, AM1: the closed views ----------------------------------------------------

EXPECTED_KEYS: dict[type[BaseModel], set[str]] = {
    views.StudentProgress: {
        "student_id",
        "from_",
        "to",
        "subject",
        "subjects",
        "attendance",
        "attendance_by_month",
        "grades",
        "homework",
        "plans",
    },
    views.ProgressSubject: {"id", "name"},
    views.ProgressAttendance: {
        "sessions",
        "present",
        "late",
        "absent",
        "not_recorded",
        "attendance_percent",
    },
    views.ProgressAttendanceMonth: {
        "month",
        "sessions",
        "present",
        "late",
        "absent",
        "not_recorded",
    },
    views.ProgressGrades: {
        "evaluation_count",
        "evaluation_average_percent",
        "assignment_count",
        "assignment_average_percent",
        "by_subject",
        "by_month",
    },
    views.ProgressGradeSubject: {
        "subject",
        "evaluation_count",
        "evaluation_average_percent",
        "assignment_count",
        "assignment_average_percent",
    },
    views.ProgressGradeMonth: {
        "month",
        "subject",
        "evaluation_count",
        "evaluation_average_percent",
        "assignment_count",
        "assignment_average_percent",
    },
    views.ProgressHomework: {
        "pending",
        "missing",
        "submitted",
        "graded",
        "late",
        "completion_percent",
    },
    views.ProgressPlans: {
        "active",
        "completed",
        "objectives_complete",
        "objectives_total",
        "milestones_overdue",
        "items",
    },
    views.ProgressPlanItem: {
        "id",
        "title",
        "subject",
        "status",
        "progress_percent",
        "objectives_complete",
        "objectives_total",
        "milestones_done",
        "milestones_total",
        "milestones_overdue",
        "target_on",
    },
    views.SessionLog: {"items", "next_cursor"},
    views.SessionLogItem: {
        "class_session_id",
        "starts_at",
        "subject",
        "session_kind",
        "tutor",
        "attendance",
        "summary",
        "published_note_count",
    },
    views.SessionLogTutor: {"display_name"},
    views.SessionLogSummary: {"topics", "performance", "homework", "next_steps"},
}


@pytest.mark.parametrize("model", list(EXPECTED_KEYS), ids=lambda m: m.__name__)
def test_the_views_are_closed_allow_lists(model):
    assert set(model.model_fields) == EXPECTED_KEYS[model]
    assert model.model_config.get("extra") == "forbid"


def test_the_progress_body_serializes_from_and_decimal_strings():
    subject = views.ProgressSubject(id=uuid.uuid4(), name="Matemáticas")
    body = views.StudentProgress(
        student_id=uuid.uuid4(),
        from_=date(2030, 4, 1),
        to=date(2031, 3, 5),
        subject=None,
        subjects=[subject],
        attendance=views.ProgressAttendance(
            sessions=3,
            present=1,
            late=1,
            absent=1,
            not_recorded=0,
            attendance_percent=Decimal("66.67"),
        ),
        attendance_by_month=[],
        grades=views.ProgressGrades(
            evaluation_count=0,
            evaluation_average_percent=None,
            assignment_count=1,
            assignment_average_percent=Decimal("80"),
            by_subject=[],
            by_month=[],
        ),
        homework=views.ProgressHomework(
            pending=0, missing=0, submitted=0, graded=0, late=0, completion_percent=None
        ),
        plans=views.ProgressPlans(
            active=0,
            completed=0,
            objectives_complete=0,
            objectives_total=0,
            milestones_overdue=0,
            items=[],
        ),
    ).model_dump(mode="json", by_alias=True)
    assert body["from"] == "2030-04-01"
    assert "from_" not in body
    assert body["attendance"]["attendance_percent"] == "66.67"
    assert body["grades"]["assignment_average_percent"] == "80.00"
    assert body["grades"]["evaluation_average_percent"] is None
