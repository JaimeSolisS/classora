"""The homework assignment rules (#82 A3): limits, the deadline instant
(end of day, DST gap and overlap), late / missing / the states, the URL rule,
the score percentage, the relation and every relation × action × state
combination of the write blocks."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import product

import pytest

from app.modules.academics import assignment_rules as rules
from app.modules.academics.assignment_rules import AssignmentRelation as R
from app.modules.academics.models import Assignment, AssignmentSubmission
from app.modules.identity.permissions import ASSIGNMENT_REVIEW, TUTOR
from app.modules.scheduling.states import SessionState

MX = "America/Mexico_City"
NY = "America/New_York"


def test_the_limits_and_codes_match_the_catalog_and_the_tables():
    assert rules.RECIPIENTS_MAX == 100
    assert (rules.TITLE_MAX, rules.INSTRUCTIONS_MAX) == (200, 4000)
    assert (rules.TEXT_MAX, rules.URL_MAX, rules.FEEDBACK_MAX) == (10000, 2000, 4000)
    assert (rules.REASON_MAX, rules.DUE_MAX_DAYS) == (500, 365)
    assert (rules.MAX_SCORE_MIN, rules.MAX_SCORE_MAX) == (Decimal("0.5"), 1000)
    assert rules.ASSIGNMENT_REVIEW == ASSIGNMENT_REVIEW
    assert rules.TUTOR_ROLE == TUTOR
    assert Assignment.__table__.c.title.type.length == rules.TITLE_MAX
    assert AssignmentSubmission.__table__.c.url.type.length == rules.URL_MAX
    assert rules.ASSIGNABLE_STATES == {
        SessionState.CONFIRMED.value,
        SessionState.COMPLETED.value,
    }
    assert rules.STATES == ("pending", "missing", "submitted", "graded")


# --- The deadline ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("due_on", "due_time", "zone", "expected"),
    [
        # No time: the end of the local day (Mexico City is UTC-6).
        (
            date(2031, 3, 5),
            None,
            MX,
            datetime(2031, 3, 6, 5, 59, 59, 999999, tzinfo=UTC),
        ),
        (date(2031, 3, 5), time(16, 0), MX, datetime(2031, 3, 5, 22, 0, tzinfo=UTC)),
        # Spring forward (New York, 2031-03-09 02:00 → 03:00): 02:30 doesn't
        # exist and moves forward by the gap (the offset from before).
        (date(2031, 3, 9), time(2, 30), NY, datetime(2031, 3, 9, 7, 30, tzinfo=UTC)),
        # Fall back (2031-11-02 02:00 → 01:00): 01:30 happens twice; the
        # first instant (fold=0, EDT).
        (date(2031, 11, 2), time(1, 30), NY, datetime(2031, 11, 2, 5, 30, tzinfo=UTC)),
        # The end of a DST day.
        (
            date(2031, 3, 9),
            None,
            NY,
            datetime(2031, 3, 10, 3, 59, 59, 999999, tzinfo=UTC),
        ),
    ],
)
def test_deadline_instant(due_on, due_time, zone, expected):
    assert rules.deadline_instant(due_on, due_time, zone) == expected


DEADLINE = datetime(2031, 3, 5, 22, 0, tzinfo=UTC)
BEFORE = DEADLINE - timedelta(microseconds=1)
AFTER = DEADLINE + timedelta(microseconds=1)


@pytest.mark.parametrize(
    ("submitted_at", "late"),
    [(None, False), (BEFORE, False), (DEADLINE, False), (AFTER, True)],
)
def test_is_late_exactly_at_the_deadline_is_on_time(submitted_at, late):
    assert rules.is_late(submitted_at, DEADLINE) is late


@pytest.mark.parametrize(
    ("submitted_at", "graded_at", "now", "state"),
    [
        (None, None, BEFORE, "pending"),
        (None, None, DEADLINE, "pending"),
        (None, None, AFTER, "missing"),
        (BEFORE, None, AFTER, "submitted"),
        (AFTER, None, AFTER, "submitted"),
        (BEFORE, AFTER, AFTER, "graded"),
        (AFTER, AFTER, AFTER, "graded"),
    ],
)
def test_submission_state(submitted_at, graded_at, now, state):
    assert rules.submission_state(submitted_at, graded_at, DEADLINE, now) == state


def test_moving_the_deadline_changes_late_and_missing():
    submitted = AFTER
    later = DEADLINE + timedelta(days=1)
    assert rules.is_late(submitted, DEADLINE)
    assert not rules.is_late(submitted, later)
    assert rules.submission_state(None, None, DEADLINE, AFTER) == "missing"
    assert rules.submission_state(None, None, later, AFTER) == "pending"


def test_due_window():
    assert rules.due_window(date(2031, 3, 5)) == (date(2031, 3, 5), date(2032, 3, 4))


# --- The URL rule ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com",
        "http://example.com/a/b?c=d#e",
        "HTTPS://Example.COM/x",
        "https://example.com:8443/path",
        "https://docs.google.com/document/d/abc/edit",
        "https://[2001:db8::1]/x",
        "https://example.com/" + "a" * (2000 - len("https://example.com/")),
    ],
)
def test_valid_urls(value):
    assert rules.validate_url(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "",
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "mailto:a@example.com",
        "/relative/path",
        "example.com",
        "//example.com/x",
        "https://",
        "https:///path",
        "https://user:pass@example.com/",
        "https://user@example.com/",
        "https://exa mple.com/",
        "https://example.com/a b",
        "https://example.com/\n",
        "https://example.com/\t",
        "https://example.com/\x00",
        "https://example.com/​",
        "https://example.com:99999/",
        "https://example.com:port/",
        "https://[::1/",
        "https:\\\\example.com",
        "https://example.com/" + "a" * (2001 - len("https://example.com/")),
    ],
)
def test_invalid_urls(value):
    assert rules.validate_url(value) == "url_invalid"


# --- Grades ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "maximum", "percent"),
    [
        ("0", "10", "0.00"),
        ("10", "10", "100.00"),
        ("1", "3", "33.33"),
        ("2", "3", "66.67"),
        ("7.5", "10", "75.00"),
        ("0.01", "1000", "0.00"),
        ("0.05", "1000", "0.01"),
        ("0.5", "0.5", "100.00"),
    ],
)
def test_score_percent_rounds_half_up(score, maximum, percent):
    assert rules.score_percent(Decimal(score), Decimal(maximum)) == Decimal(percent)


def test_score_percent_never_takes_a_float():
    with pytest.raises(TypeError):
        rules.score_percent(7.5, Decimal(10))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        rules.score_percent(Decimal(1), Decimal(0))


def test_average_percent():
    assert rules.average_percent([]) is None
    assert rules.average_percent([Decimal("50"), Decimal("75")]) == Decimal("62.50")
    assert rules.average_percent([Decimal("33.33")] * 3) == Decimal("33.33")


@pytest.mark.parametrize(
    ("value", "ok"),
    [("1", True), ("1.1", True), ("1.10", True), ("1.005", False), ("NaN", False)],
)
def test_has_cents_only(value, ok):
    assert rules.has_cents_only(Decimal(value)) is ok


# --- The relation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("facts", "relation"),
    [
        ({"tutor": True, "is_creator": True, "student_assigned": True}, R.CREATOR),
        (
            {"tutor": True, "is_creator": True, "student_assigned": False},
            R.FORMER_CREATOR,
        ),
        # The creator relation wins over everything else.
        (
            {
                "tutor": True,
                "is_creator": True,
                "student_assigned": False,
                "reviewer": True,
                "guardian": True,
            },
            R.FORMER_CREATOR,
        ),
        # Without the tutor role, a creator is nobody special.
        ({"tutor": False, "is_creator": True, "student_assigned": False}, R.NONE),
        ({"student_self": True}, R.STUDENT_SELF),
        ({"student_self": True, "reviewer": True}, R.STUDENT_SELF),
        ({"reviewer": True}, R.REVIEWER),
        ({"reviewer": True, "tutor": True, "student_assigned": True}, R.REVIEWER),
        ({"tutor": True, "student_assigned": True}, R.ASSIGNED_TUTOR),
        ({"tutor": False, "student_assigned": True}, R.NONE),
        ({"tutor": True, "student_assigned": True, "guardian": True}, R.ASSIGNED_TUTOR),
        ({"guardian": True}, R.GUARDIAN),
        ({}, R.NONE),
    ],
)
def test_assignment_relation(facts, relation):
    values = {
        "tutor": False,
        "is_creator": False,
        "student_assigned": False,
        "reviewer": False,
    } | facts
    assert rules.assignment_relation(**values) is relation


# The matrix (E6), one row per relation: the access answer per action.
ACCESS = {
    R.CREATOR: {"edit": None, "delete": None, "submit": None, "grade": None},
    R.FORMER_CREATOR: dict.fromkeys(rules.ACTIONS, "assignment_read_only"),
    R.STUDENT_SELF: {
        "edit": "not_found",
        "delete": "not_found",
        "submit": None,
        "grade": "not_found",
    },
    R.REVIEWER: dict.fromkeys(rules.ACTIONS, "assignment_creator_only"),
    R.ASSIGNED_TUTOR: {
        "edit": "not_found",
        "delete": "not_found",
        "submit": "assignment_creator_only",
        "grade": "assignment_creator_only",
    },
    R.GUARDIAN: {
        "edit": "not_found",
        "delete": "not_found",
        "submit": "submission_student_only",
        "grade": "not_found",
    },
    R.NONE: dict.fromkeys(rules.ACTIONS, "not_found"),
}


@pytest.mark.parametrize(("relation", "action"), list(product(list(R), rules.ACTIONS)))
def test_access_block_covers_every_cell(relation, action):
    assert rules.access_block(relation, action) == ACCESS[relation][action]


def _expected_state(
    action, *, student_path, submitted, graded, passed, has_submissions
):
    if action == "submit":
        if graded:
            return "submission_graded"
        if student_path and submitted and passed:
            return "submission_locked"
        return None
    if action == "grade":
        return None if submitted else "submission_required"
    if action == "delete":
        return "assignment_has_submissions" if has_submissions else None
    return None


FLAGS = list(product([False, True], repeat=5))


@pytest.mark.parametrize("action", rules.ACTIONS)
@pytest.mark.parametrize(
    ("student_path", "submitted", "graded", "passed", "has_submissions"), FLAGS
)
def test_state_block(action, student_path, submitted, graded, passed, has_submissions):
    assert rules.state_block(
        action,
        student_path=student_path,
        submitted=submitted,
        graded=graded,
        deadline_passed=passed,
        has_submissions=has_submissions,
    ) == _expected_state(
        action,
        student_path=student_path,
        submitted=submitted,
        graded=graded,
        passed=passed,
        has_submissions=has_submissions,
    )


@pytest.mark.parametrize(
    ("relation", "action", "deleted", "submitted", "graded", "passed"),
    list(
        product(
            list(R),
            rules.ACTIONS,
            [False, True],
            [False, True],
            [False, True],
            [False, True],
        )
    ),
)
def test_write_block_combines_in_the_route_order(
    relation, action, deleted, submitted, graded, passed
):
    found = rules.write_block(
        relation,
        action,
        deleted=deleted,
        submitted=submitted,
        graded=graded,
        deadline_passed=passed,
        has_submissions=submitted,
    )
    if deleted:
        assert found == "not_found"
    elif ACCESS[relation][action] is not None:
        assert found == ACCESS[relation][action]
    else:
        assert found == _expected_state(
            action,
            student_path=relation is R.STUDENT_SELF,
            submitted=submitted,
            graded=graded,
            passed=passed,
            has_submissions=submitted,
        )


def test_only_the_student_path_locks_at_the_deadline():
    """E4: the creator records handed-in work at any time (no lock); the
    student's existing submission locks, a missing one is still accepted."""
    after = {"deadline_passed": True}
    assert rules.write_block(R.CREATOR, "submit", submitted=True, **after) is None
    assert (
        rules.write_block(R.STUDENT_SELF, "submit", submitted=True, **after)
        == "submission_locked"
    )
    assert rules.write_block(R.STUDENT_SELF, "submit", submitted=False, **after) is None
    for relation in (R.CREATOR, R.STUDENT_SELF):
        assert (
            rules.write_block(relation, "submit", submitted=True, graded=True)
            == "submission_graded"
        )


@pytest.mark.parametrize(
    ("value", "cleaned"), [(None, None), ("", None), ("  ", None), (" a b ", "a b")]
)
def test_clean(value, cleaned):
    assert rules.clean(value) == cleaned


def test_the_family_shape_is_a_closed_allow_list():
    """E2: one closed shape for every audience: no user ids, no roster, no
    counts, no change reason, no session id."""
    from app.workflows.assignment_schemas import (
        StudentAssignment,
        StudentAssignmentList,
        StudentAssignmentSession,
        StudentSubmission,
    )

    assert set(StudentAssignment.model_fields) == {
        "id",
        "subject",
        "title",
        "instructions",
        "due_on",
        "due_time",
        "deadline_at",
        "max_score",
        "creator",
        "class_session",
        "submission",
        "can_submit",
        "can_grade",
    }
    assert set(StudentSubmission.model_fields) == {
        "state",
        "is_late",
        "submitted_at",
        "submitted_via",
        "text",
        "url",
        "score",
        "score_percent",
        "feedback",
        "graded_at",
        "updated",
        "version",
    }
    assert set(StudentAssignmentSession.model_fields) == {"starts_at"}
    assert set(StudentAssignmentList.model_fields) == {
        "items",
        "summary",
        "next_cursor",
    }
    for model in (StudentAssignment, StudentSubmission, StudentAssignmentSession):
        assert model.model_config.get("extra") == "forbid"
