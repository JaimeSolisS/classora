"""The progress report rules (#83 A3, I4): limits, periods, the relation,
the working read rule, every relation × status × deleted × action × owner
× reviewer combination of the blocks, the can flags, the facts key sets,
the closed family shapes and the inbox summary keys."""

from datetime import date, timedelta
from itertools import product

import pytest

from app.modules.academics import progress_report_facts as facts
from app.modules.academics import progress_report_rules as rules
from app.modules.academics.models import (
    PROGRESS_REPORT_KINDS,
    PROGRESS_REPORT_PROTECTED,
    PROGRESS_REPORT_STATUSES,
    ProgressReport,
)
from app.modules.academics.progress_report_rules import ReportRelation as R
from app.modules.identity.permissions import OWNER, PROGRESS_REPORT_REVIEW, TUTOR
from app.workflows.progress_report_approval import SUMMARY_KEYS
from app.workflows.progress_report_schemas import (
    FactsAssignments,
    FactsAttendance,
    FactsEvaluationEntry,
    FactsEvaluations,
    FactsEvaluationSubject,
    FactsLearningPlan,
    FactsPeriod,
    ProgressReportFacts,
    ProgressReportFamilyListItem,
    ProgressReportFamilyPage,
    ProgressReportFamilyView,
    ProgressReportSubject,
)

MONDAY = date(2031, 3, 3)


def test_the_limits_and_codes_match_the_catalog_and_the_table():
    assert (rules.SUMMARY_MAX, rules.SECTION_MAX, rules.REASON_MAX) == (
        4000,
        2000,
        1000,
    )
    assert rules.ON_DEMAND_MAX_DAYS == 366
    assert (rules.FACT_EVALUATIONS_MAX, rules.FACT_PLANS_MAX) == (50, 20)
    assert (rules.LIST_LIMIT_MAX, rules.LIST_LIMIT_DEFAULT) == (50, 20)
    assert rules.PROGRESS_REPORT_REVIEW == PROGRESS_REPORT_REVIEW
    assert (rules.TUTOR_ROLE, rules.OWNER_ROLE) == (TUTOR, OWNER)
    assert rules.KINDS == PROGRESS_REPORT_KINDS
    assert rules.STATUSES == PROGRESS_REPORT_STATUSES
    # A1: the trigger freezes the content and the approval, not the review
    # bookkeeping a retraction writes.
    columns = set(ProgressReport.__table__.c.keys())
    assert set(PROGRESS_REPORT_PROTECTED) <= columns
    assert not {
        "status",
        "version",
        "updated_at",
        "retracted_at",
        "retracted_by_user_id",
        "retract_reason",
    } & set(PROGRESS_REPORT_PROTECTED)


# --- Periods ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "start", "end", "expected"),
    [
        ("weekly", MONDAY, None, (MONDAY, MONDAY + timedelta(days=6))),
        ("weekly", MONDAY, MONDAY + timedelta(days=6), (MONDAY, date(2031, 3, 9))),
        ("weekly", MONDAY + timedelta(days=1), None, "period_invalid"),  # Tuesday
        ("weekly", MONDAY - timedelta(days=1), None, "period_invalid"),  # Sunday
        ("weekly", MONDAY, MONDAY + timedelta(days=5), "period_invalid"),
        ("monthly", date(2031, 2, 1), None, (date(2031, 2, 1), date(2031, 2, 28))),
        ("monthly", date(2032, 2, 1), None, (date(2032, 2, 1), date(2032, 2, 29))),
        ("monthly", date(2031, 12, 1), None, (date(2031, 12, 1), date(2031, 12, 31))),
        (
            "monthly",
            date(2031, 4, 1),
            date(2031, 4, 30),
            (date(2031, 4, 1), date(2031, 4, 30)),
        ),
        ("monthly", date(2031, 4, 2), None, "period_invalid"),
        ("monthly", date(2031, 4, 1), date(2031, 4, 29), "period_invalid"),
        ("on_demand", MONDAY, MONDAY, (MONDAY, MONDAY)),
        ("on_demand", MONDAY, MONDAY + timedelta(days=365), (MONDAY, date(2032, 3, 2))),
        ("on_demand", MONDAY, MONDAY + timedelta(days=366), "period_invalid"),  # 367
        ("on_demand", MONDAY, MONDAY - timedelta(days=1), "period_invalid"),
        ("on_demand", MONDAY, None, "period_invalid"),
        ("yearly", MONDAY, None, "period_invalid"),
    ],
)
def test_period_for(kind, start, end, expected):
    assert rules.period_for(kind, start, end) == expected


def test_period_in_future():
    assert rules.period_in_future(date(2031, 3, 6), date(2031, 3, 5))
    assert not rules.period_in_future(date(2031, 3, 5), date(2031, 3, 5))
    assert not rules.period_in_future(date(2031, 3, 4), date(2031, 3, 5))


# --- Relation and reads -----------------------------------------------------------


@pytest.mark.parametrize(
    ("is_author", "tutor", "current", "reviewer", "expected"),
    [
        (True, True, True, False, R.AUTHOR),
        (True, True, True, True, R.AUTHOR),
        (True, True, False, False, R.FORMER_AUTHOR),
        (True, True, False, True, R.FORMER_AUTHOR),
        # An author who lost the tutor role is no author any more.
        (True, False, False, False, R.NONE),
        (True, False, False, True, R.REVIEWER),
        (False, True, True, True, R.REVIEWER),
        (False, False, False, True, R.REVIEWER),
        (False, True, True, False, R.ASSIGNED_TUTOR),
        (False, True, False, False, R.NONE),
        (False, False, False, False, R.NONE),
    ],
)
def test_report_relation(is_author, tutor, current, reviewer, expected):
    assert (
        rules.report_relation(
            is_author=is_author,
            tutor=tutor,
            student_current=current,
            reviewer=reviewer,
        )
        is expected
    )


def _readable(relation, status, deleted, reviewer):
    """The matrix's working-read columns, written independently."""
    if deleted:
        return False
    if relation in (R.AUTHOR, R.FORMER_AUTHOR):
        return True
    return reviewer and status != "draft"


@pytest.mark.parametrize(
    ("relation", "status", "deleted", "reviewer"),
    list(product(R, PROGRESS_REPORT_STATUSES, (False, True), (False, True))),
)
def test_can_read(relation, status, deleted, reviewer):
    assert rules.can_read(
        relation, status=status, deleted=deleted, reviewer=reviewer
    ) == _readable(relation, status, deleted, reviewer)


# --- Blocks -----------------------------------------------------------------------


def _expected(relation, status, deleted, action, reviewer, owner):
    """The matrix and C3/C4/D2-D5, written independently of the rules."""
    if not _readable(relation, status, deleted, reviewer):
        return "not_found"
    if action in ("edit", "submit", "delete"):
        if relation is R.FORMER_AUTHOR:
            return "progress_report_read_only"
        if relation is R.REVIEWER:
            return "progress_report_author_only"
        if relation is not R.AUTHOR:
            return "not_found"
        if status in ("draft", "changes_requested"):
            return None
        if status == "submitted":
            return "progress_report_under_review"
        return "progress_report_closed"
    if not reviewer:
        return "permission_denied"
    if relation in (R.AUTHOR, R.FORMER_AUTHOR) and not owner:
        return "progress_report_self_review"
    if action == "retract":
        if status in ("submitted", "approved_for_family"):
            return None
        if status == "retracted":
            return "progress_report_closed"
        return "progress_report_not_submitted"
    return None if status == "submitted" else "progress_report_not_submitted"


def _consistent(relation: R, reviewer: bool) -> bool:
    """:func:`report_relation` never gives ``reviewer`` without the
    permission, nor ``assigned_tutor`` / ``none`` with it."""
    if relation is R.REVIEWER:
        return reviewer
    if relation in (R.ASSIGNED_TUTOR, R.NONE):
        return not reviewer
    return True


@pytest.mark.parametrize(
    ("relation", "status", "deleted", "action", "reviewer", "owner"),
    [
        case
        for case in product(
            R,
            PROGRESS_REPORT_STATUSES,
            (False, True),
            rules.ACTIONS,
            (False, True),
            (False, True),
        )
        if _consistent(case[0], case[4])
    ],
)
def test_every_block(relation, status, deleted, action, reviewer, owner):
    assert rules.block(
        relation, status, deleted, action, reviewer=reviewer, is_owner_role=owner
    ) == _expected(relation, status, deleted, action, reviewer, owner)


def test_the_access_block_comes_before_the_state_block():
    # A former author on a submitted report: read-only (403) before the 409.
    assert (
        rules.access_block(R.FORMER_AUTHOR, "edit", reviewer=False, is_owner_role=False)
        == "progress_report_read_only"
    )
    # A reviewer on their own approved report: self-review before closed.
    assert (
        rules.block(
            R.AUTHOR,
            "approved_for_family",
            False,
            "retract",
            reviewer=True,
            is_owner_role=False,
        )
        == "progress_report_self_review"
    )
    # The Owner reviews their own report.
    assert (
        rules.block(
            R.AUTHOR, "submitted", False, "approve", reviewer=True, is_owner_role=True
        )
        is None
    )


def test_can_flags():
    author = rules.can_flags(
        R.AUTHOR, "draft", reviewer=False, is_owner_role=False, author_current=True
    )
    assert (author.can_edit, author.can_submit, author.can_delete) == (True,) * 3
    assert not (author.can_approve or author.can_request_changes or author.can_retract)
    reviewer = rules.can_flags(
        R.REVIEWER, "submitted", reviewer=True, is_owner_role=False, author_current=True
    )
    assert (
        reviewer.can_approve,
        reviewer.can_request_changes,
        reviewer.can_retract,
    ) == (
        True,
        True,
        True,
    )
    assert not (reviewer.can_edit or reviewer.can_submit or reviewer.can_delete)
    # D4: the author no longer teaches the student.
    gone = rules.can_flags(
        R.REVIEWER,
        "submitted",
        reviewer=True,
        is_owner_role=False,
        author_current=False,
    )
    assert (gone.can_approve, gone.can_request_changes, gone.can_retract) == (
        True,
        False,
        True,
    )
    approved = rules.can_flags(
        R.REVIEWER,
        "approved_for_family",
        reviewer=True,
        is_owner_role=False,
        author_current=True,
    )
    assert (approved.can_approve, approved.can_retract) == (False, True)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("   ", None), (" a ", "a"), ("x\n", "x")],
)
def test_clean(value, expected):
    assert rules.clean(value) == expected


# --- Facts and the family shapes --------------------------------------------------


def _fields(model) -> set[str]:
    return set(model.model_fields)


def test_the_facts_key_sets_are_closed():
    """B2: a unit test asserts the exact key sets; the view models are the
    same closed allow-lists."""
    assert facts.FACT_KEYS == {
        "schema_version",
        "period",
        "subject",
        "attendance",
        "evaluations",
        "assignments",
        "learning_plans",
        "learning_plans_truncated",
    }
    assert _fields(ProgressReportFacts) == facts.FACT_KEYS
    assert _fields(FactsPeriod) == facts.PERIOD_KEYS == {"start", "end"}
    assert _fields(ProgressReportSubject) == facts.SUBJECT_KEYS == {"id", "name"}
    assert (
        _fields(FactsAttendance)
        == facts.ATTENDANCE_KEYS
        == {
            "sessions",
            "present",
            "late",
            "absent",
            "not_recorded",
        }
    )
    assert (
        _fields(FactsEvaluations)
        == facts.EVALUATION_KEYS
        == {
            "count",
            "average_percent",
            "by_subject",
            "entries",
            "truncated",
        }
    )
    assert _fields(FactsEvaluationSubject) == facts.EVALUATION_SUBJECT_KEYS
    assert (
        _fields(FactsEvaluationEntry)
        == facts.EVALUATION_ENTRY_KEYS
        == {
            "evaluated_on",
            "subject",
            "evaluation_kind",
            "template_name",
            "score_percent",
        }
    )
    assert (
        _fields(FactsAssignments)
        == facts.ASSIGNMENT_KEYS
        == {
            "count",
            "pending",
            "missing",
            "submitted",
            "graded",
            "late",
            "average_percent",
        }
    )
    assert (
        _fields(FactsLearningPlan)
        == facts.PLAN_KEYS
        == {
            "title",
            "subject",
            "status",
            "progress_percent",
            "objectives_completed",
            "objectives_total",
            "milestones_done",
            "milestones_total",
        }
    )
    for model in (
        ProgressReportFacts,
        FactsPeriod,
        FactsAttendance,
        FactsEvaluations,
        FactsEvaluationSubject,
        FactsEvaluationEntry,
        FactsAssignments,
        FactsLearningPlan,
    ):
        assert model.model_config.get("extra") == "forbid"


def test_facts_document_is_the_closed_shape():
    document = facts.facts_document(
        start=MONDAY,
        end=MONDAY + timedelta(days=6),
        subject=None,
        attendance={
            "sessions": 2,
            "present": 1,
            "late": 0,
            "absent": 1,
            "not_recorded": 0,
        },
        academic={
            "evaluations": {
                "count": 0,
                "average_percent": None,
                "by_subject": [],
                "entries": [],
                "truncated": False,
            },
            "assignments": {
                "count": 0,
                "pending": 0,
                "missing": 0,
                "submitted": 0,
                "graded": 0,
                "late": 0,
                "average_percent": None,
            },
            "learning_plans": [],
            "learning_plans_truncated": False,
        },
    )
    assert set(document) == facts.FACT_KEYS
    assert document["schema_version"] == 1
    assert document["period"] == {"start": "2031-03-03", "end": "2031-03-09"}
    parsed = ProgressReportFacts.model_validate(document)
    assert parsed.model_dump(mode="json")["attendance"]["absent"] == 1


def test_the_family_shapes_are_closed_allow_lists():
    """F2: never the status, version, ids of people, the approver,
    reasons, retraction data, submitted_at or the preview."""
    assert _fields(ProgressReportFamilyView) == {
        "id",
        "student_id",
        "kind",
        "period_start",
        "period_end",
        "subject",
        "author",
        "summary",
        "strengths",
        "areas_for_improvement",
        "next_steps",
        "facts",
        "approved_at",
    }
    assert _fields(ProgressReportFamilyListItem) == {
        "id",
        "kind",
        "period_start",
        "period_end",
        "subject",
        "author",
        "approved_at",
    }
    # AM2.
    assert _fields(ProgressReportFamilyPage) == {
        "items",
        "next_cursor",
        "can_create",
        "creatable_subjects",
    }
    author = ProgressReportFamilyView.model_fields["author"].annotation
    assert set(author.model_fields) == {"display_name"}  # type: ignore[union-attr]
    for model in (
        ProgressReportFamilyView,
        ProgressReportFamilyListItem,
        ProgressReportFamilyPage,
    ):
        assert model.model_config.get("extra") == "forbid"


def test_the_inbox_summary_keys():
    """B4, AM1: no narrative, facts or reasons."""
    assert SUMMARY_KEYS == {
        "report_id",
        "student",
        "author",
        "kind",
        "period_start",
        "period_end",
        "subject",
        "submitted_at",
    }
