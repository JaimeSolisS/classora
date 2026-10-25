"""Evaluation rules (#80 A3, C1, D, E, F, I4): table-driven, no database.

Every relation x status x voided x action for the read, write, state and
``can_*`` rules (deleted evaluations never reach them: ``404`` first), the
decimal totals (0, the maximum, 1/3, 20 criteria at 1000, never a float),
the average, ``period_for``, the evaluable session states, the bodies'
schema checks and the closed history allow-lists.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.authz import Principal
from app.modules.academics import evaluation_rules as rules
from app.modules.academics.evaluation_rules import EvaluationRelation as Relation
from app.modules.academics.models import EVALUATION_KINDS, EVALUATION_STATUSES
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_PERMISSIONS,
    STUDENT,
    TUTOR,
)
from app.modules.scheduling.states import SessionState
from app.workflows.evaluation_schemas import (
    AcademicHistory,
    EvaluationCreate,
    EvaluationPatch,
    EvaluationTemplateCreate,
    EvaluationTemplateReplace,
    EvaluationVoid,
    HistoryEntry,
    HistoryGroup,
    HistorySubjectGroup,
)

ME = uuid.uuid4()
OTHER = uuid.uuid4()
D = Decimal


def principal(*roles: str, user_id: uuid.UUID = ME) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS[role]
    return Principal(
        user_id=user_id, roles=frozenset(roles), permissions=frozenset(permissions)
    )


def test_the_limits():
    assert rules.CRITERIA_MAX == 20
    assert (rules.MAX_SCORE_MIN, rules.MAX_SCORE_MAX) == (D("0.5"), D("1000"))
    assert (rules.FEEDBACK_MAX, rules.COMMENT_MAX, rules.REASON_MAX) == (
        4000,
        1000,
        500,
    )
    assert rules.HISTORY_MAX_ENTRIES == 500
    assert rules.KINDS == EVALUATION_KINDS
    assert rules.STATUSES == EVALUATION_STATUSES


def test_the_evaluable_states_match_the_scheduling_states():
    values = {state.value for state in SessionState}
    assert rules.EVALUABLE_STATES <= values
    assert rules.EVALUABLE_STATES == {
        SessionState.CONFIRMED.value,
        SessionState.COMPLETED.value,
    }


NOW = datetime(2031, 3, 5, 18, 0, tzinfo=UTC)


@pytest.mark.parametrize("state", [state.value for state in SessionState])
@pytest.mark.parametrize("started", [True, False])
def test_only_started_confirmed_or_completed_sessions_are_evaluable(state, started):
    starts_at = NOW - timedelta(minutes=1) if started else NOW + timedelta(minutes=1)
    expected = state in {"confirmed", "completed"} and started
    assert rules.is_evaluable(state, starts_at, NOW) is expected
    assert rules.is_evaluable(state, NOW, NOW) is (state in {"confirmed", "completed"})


# --- E2: decimal totals ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("pairs", "expected"),
    [
        ([(D(0), D(10))], (D("0.00"), D("10.00"), D("0.00"))),
        ([(D(10), D(10)), (D("0.5"), D("0.5"))], (D("10.50"), D("10.50"), D("100.00"))),
        # 1/3: 33.333… rounds down; 2/3: 66.666… rounds half-up to 66.67.
        ([(D(1), D(3))], (D("1.00"), D("3.00"), D("33.33"))),
        ([(D(2), D(3))], (D("2.00"), D("3.00"), D("66.67"))),
        # Exactly half a cent rounds up.
        ([(D("0.01"), D(8))], (D("0.01"), D("8.00"), D("0.13"))),
        ([(D(1), D(8))], (D("1.00"), D("8.00"), D("12.50"))),
        ([(D("1000"), D("1000"))] * 20, (D("20000.00"), D("20000.00"), D("100.00"))),
        ([(D("999.90"), D("1000"))] * 20, (D("19998.00"), D("20000.00"), D("99.99"))),
        # 99.999 % rounds half-up to 100.00.
        ([(D("999.99"), D("1000"))] * 20, (D("19999.80"), D("20000.00"), D("100.00"))),
    ],
)
def test_score_totals(pairs, expected):
    totals = rules.score_totals(pairs)
    assert totals == expected
    assert all(isinstance(value, Decimal) for value in totals)
    assert [str(value) for value in totals] == [str(value) for value in expected]


def test_score_totals_never_takes_a_float():
    with pytest.raises(TypeError):
        rules.score_totals([(1 / 3, D(1))])  # type: ignore[list-item]
    with pytest.raises(ValueError):
        rules.score_totals([])


def test_the_average_percent():
    assert rules.average_percent([]) is None
    assert rules.average_percent([D("100.00"), D("50.00"), D("50.00")]) == D("66.67")
    assert rules.average_percent([D("33.33"), D("33.34")]) == D("33.34")
    assert str(rules.average_percent([D("80")])) == "80.00"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(D("1"), True), (D("1.1"), True), (D("1.10"), True), (D("1.005"), False)],
)
def test_two_places_at_most(value, expected):
    assert rules.has_cents_only(value) is expected


# --- E5: the relation -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("roles", "author", "assigned", "expected"),
    [
        ((TUTOR,), ME, True, Relation.AUTHOR),
        ((TUTOR,), ME, False, Relation.FORMER_AUTHOR),
        ((TUTOR,), OTHER, True, Relation.ASSIGNED_TUTOR),
        ((TUTOR,), OTHER, False, Relation.NONE),
        # A tutor who lost the role relates to nothing (404 everywhere).
        ((), ME, True, Relation.NONE),
        ((OWNER,), OTHER, False, Relation.REVIEWER),
        ((ADMINISTRATOR,), OTHER, False, Relation.REVIEWER),
        ((ACADEMIC_COORDINATOR,), OTHER, False, Relation.REVIEWER),
        ((FINANCE_STAFF,), OTHER, False, Relation.NONE),
        ((GUARDIAN,), OTHER, False, Relation.NONE),
        ((STUDENT,), OTHER, False, Relation.NONE),
        # Several roles: the first that applies (a reviewer reads more than
        # an assigned tutor).
        ((TUTOR, ACADEMIC_COORDINATOR), ME, False, Relation.FORMER_AUTHOR),
        ((TUTOR, ACADEMIC_COORDINATOR), OTHER, True, Relation.REVIEWER),
        ((TUTOR, FINANCE_STAFF), OTHER, True, Relation.ASSIGNED_TUTOR),
        ((OWNER,), ME, False, Relation.REVIEWER),
    ],
)
def test_the_relation(roles, author, assigned, expected):
    assert (
        rules.evaluation_relation(
            principal(*roles), author_user_id=author, student_assigned=assigned
        )
        is expected
    )


def _expected_read(relation: Relation, status: str, voided: bool) -> bool:
    if relation in (Relation.AUTHOR, Relation.FORMER_AUTHOR):
        return True
    if status == "draft" or relation is Relation.NONE:
        return False
    if relation is Relation.REVIEWER:
        return True
    return not voided


STATES = [("draft", False), ("final", False), ("final", True)]


@pytest.mark.parametrize("relation", list(Relation))
@pytest.mark.parametrize(("status", "voided"), STATES)
def test_the_reads(relation, status, voided):
    assert rules.can_read(relation, status=status, voided=voided) is _expected_read(
        relation, status, voided
    )


def _expected_write(action: str, relation: Relation, reviewer: bool) -> str | None:
    if action == "void":
        return None if reviewer else "permission_denied"
    if relation is Relation.AUTHOR:
        return None
    if relation is Relation.FORMER_AUTHOR:
        return "evaluation_read_only"
    return "evaluation_author_only"


def _expected_state(action: str, status: str, voided: bool) -> str | None:
    if action == "edit":
        return "evaluation_voided" if voided else None
    if action == "finalize":
        return None if status == "draft" else "evaluation_not_draft"
    if action == "delete":
        return None if status == "draft" else "evaluation_final"
    if status != "final":
        return "evaluation_not_final"
    return "evaluation_voided" if voided else None


@pytest.mark.parametrize("action", rules.ACTIONS)
@pytest.mark.parametrize("relation", list(Relation))
@pytest.mark.parametrize("reviewer", [True, False])
def test_the_write_block(action, relation, reviewer):
    assert rules.write_block(action, relation, reviewer=reviewer) == _expected_write(
        action, relation, reviewer
    )


@pytest.mark.parametrize("action", rules.ACTIONS)
@pytest.mark.parametrize(("status", "voided"), STATES)
def test_the_state_block(action, status, voided):
    assert rules.state_block(action, status=status, voided=voided) == _expected_state(
        action, status, voided
    )


@pytest.mark.parametrize("action", rules.ACTIONS)
@pytest.mark.parametrize("relation", list(Relation))
@pytest.mark.parametrize("reviewer", [True, False])
@pytest.mark.parametrize(("status", "voided"), STATES)
def test_the_can_flags(action, relation, reviewer, status, voided):
    expected = (
        _expected_read(relation, status, voided)
        and _expected_write(action, relation, reviewer) is None
        and _expected_state(action, status, voided) is None
    )
    assert (
        rules.can(action, relation, reviewer=reviewer, status=status, voided=voided)
        is expected
    )


def test_the_matrix_rows():
    """The groomed permission matrix, row by row."""
    # The author with the student assigned: edits, finalizes, deletes own.
    for action in ("edit", "finalize", "delete"):
        assert rules.write_block(action, Relation.AUTHOR, reviewer=False) is None
        assert (
            rules.write_block(action, Relation.ASSIGNED_TUTOR, reviewer=False)
            == "evaluation_author_only"
        )
        assert (
            rules.write_block(action, Relation.FORMER_AUTHOR, reviewer=False)
            == "evaluation_read_only"
        )
        assert (
            rules.write_block(action, Relation.REVIEWER, reviewer=True)
            == "evaluation_author_only"
        )
    # Only reviewers void.
    assert rules.write_block("void", Relation.REVIEWER, reviewer=True) is None
    assert (
        rules.write_block("void", Relation.AUTHOR, reviewer=False)
        == "permission_denied"
    )
    # Drafts: only their author reads them; staff never.
    assert not rules.can_read(Relation.REVIEWER, status="draft", voided=False)
    assert not rules.can_read(Relation.ASSIGNED_TUTOR, status="draft", voided=False)
    assert rules.can_read(Relation.FORMER_AUTHOR, status="draft", voided=False)
    # Voided: the reviewer and the author; never another tutor.
    assert rules.can_read(Relation.REVIEWER, status="final", voided=True)
    assert rules.can_read(Relation.AUTHOR, status="final", voided=True)
    assert not rules.can_read(Relation.ASSIGNED_TUTOR, status="final", voided=True)


# --- E3: periods ----------------------------------------------------------------------


@dataclass(frozen=True)
class P:
    name: str
    starts_on: date
    ends_on: date


TERMS = [
    P("T1", date(2030, 9, 1), date(2030, 12, 20)),
    P("T2", date(2031, 1, 7), date(2031, 3, 31)),
]


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2030, 9, 1), "T1"),
        (date(2030, 12, 20), "T1"),
        (date(2030, 12, 21), None),
        (date(2031, 1, 7), "T2"),
        (date(2031, 3, 31), "T2"),
        (date(2031, 4, 1), None),
        (date(2030, 8, 31), None),
    ],
)
def test_period_for(day, expected):
    found = rules.period_for(day, TERMS)
    assert (None if found is None else found.name) == expected
    assert rules.period_for(day, []) is None


# --- The bodies -----------------------------------------------------------------------


def _codes(error: ValidationError) -> list[str]:
    return [item["type"] for item in error.errors()]


def _template(**values: object) -> dict[str, object]:
    return {
        "name": "Diagnóstico",
        "kind": "diagnostic",
        "criteria": [{"title": "Lectura", "max_score": "10"}],
    } | values


def test_the_template_body():
    body = EvaluationTemplateCreate.model_validate(
        _template(
            name="  Diagnóstico  ",
            criteria=[
                {"title": " Lectura ", "description": "  ", "max_score": 10},
                {"title": "Escritura", "max_score": "0.5"},
                {"title": "Cálculo", "max_score": 1000},
            ],
        )
    )
    assert body.name == "Diagnóstico"
    assert [c.title for c in body.criteria] == ["Lectura", "Escritura", "Cálculo"]
    assert body.criteria[0].description is None
    assert [c.max_score for c in body.criteria] == [D(10), D("0.5"), D(1000)]
    for values, code in (
        ({"name": "   "}, "name_required"),
        ({"name": "x" * 121}, "too_long"),
        ({"criteria": []}, "criteria_required"),
        (
            {"criteria": [{"title": f"C{i}", "max_score": 1} for i in range(21)]},
            "criteria_too_many",
        ),
        (
            {
                "criteria": [
                    {"title": "Lectura", "max_score": 1},
                    {"title": "LECTURA", "max_score": 1},
                ]
            },
            "duplicate_title",
        ),
        ({"criteria": [{"title": "A", "max_score": "0.49"}]}, "max_score_out_of_range"),
        (
            {"criteria": [{"title": "A", "max_score": 1000.01}]},
            "max_score_out_of_range",
        ),
        ({"criteria": [{"title": "A", "max_score": "1.005"}]}, "score_precision"),
        ({"criteria": [{"title": "A", "max_score": "abc"}]}, "decimal_invalid"),
        ({"criteria": [{"title": "A", "max_score": "NaN"}]}, "decimal_invalid"),
        ({"criteria": [{"title": " ", "max_score": 1}]}, "title_required"),
        ({"kind": "quiz"}, "literal_error"),
        ({"extra": 1}, "extra_forbidden"),
    ):
        with pytest.raises(ValidationError) as caught:
            EvaluationTemplateCreate.model_validate(_template(**values))
        assert code in _codes(caught.value), (values, _codes(caught.value))
    replace = EvaluationTemplateReplace.model_validate(
        _template(version=2, is_active=False)
    )
    assert (replace.version, replace.is_active) == (2, False)


def test_the_evaluation_bodies():
    student, template = uuid.uuid4(), uuid.uuid4()
    body = EvaluationCreate.model_validate(
        {
            "student_id": str(student),
            "template_id": str(template),
            "scores": [
                {"position": 1, "score": "7.5", "comment": "  bien "},
                {"position": 2},
            ],
            "feedback": "  ",
        }
    )
    assert body.scores[0].score == D("7.5")
    assert body.scores[0].comment == "bien"
    assert body.scores[1].model_fields_set == {"position"}
    assert body.feedback is None
    for values, code in (
        ({"scores": [{"position": 1, "score": "7.555"}]}, "score_precision"),
        ({"feedback": "x" * 4001}, "too_long"),
        ({"scores": [{"position": 1, "comment": "x" * 1001}]}, "too_long"),
        ({"status": "final"}, "extra_forbidden"),
    ):
        with pytest.raises(ValidationError) as caught:
            EvaluationCreate.model_validate(
                {"student_id": str(student), "template_id": str(template)} | values
            )
        assert code in _codes(caught.value)
    # PATCH: the links are immutable (sending one is an unknown field).
    for field in ("template_id", "student_id", "subject_id", "class_session_id"):
        with pytest.raises(ValidationError) as caught:
            EvaluationPatch.model_validate({"version": 1, field: str(uuid.uuid4())})
        assert _codes(caught.value) == ["extra_forbidden"]
    with pytest.raises(ValidationError) as caught:
        EvaluationPatch.model_validate({})
    assert _codes(caught.value) == ["missing"]
    patch = EvaluationPatch.model_validate({"version": 2, "change_reason": "  x  "})
    assert patch.change_reason == "x"
    assert EvaluationVoid.model_validate({"version": 1, "reason": " "}).reason is None


def test_the_history_shapes_are_closed_allow_lists():
    assert set(AcademicHistory.model_fields) == {
        "student_id",
        "group_by",
        "truncated",
        "entries",
        "groups",
    }
    assert set(HistoryEntry.model_fields) == {
        "kind",
        "id",
        "evaluated_on",
        "subject",
        "evaluation_kind",
        "template_name",
        "author",
        "criteria",
        "score_total",
        "max_total",
        "score_percent",
        "feedback",
        "course",
        "finalized_at",
        "updated",
    }
    assert set(HistoryGroup.model_fields) == {"period", "subjects"}
    assert set(HistorySubjectGroup.model_fields) == {
        "subject",
        "count",
        "average_percent",
        "entry_ids",
    }
    for model in (AcademicHistory, HistoryEntry, HistoryGroup, HistorySubjectGroup):
        assert model.model_config.get("extra") == "forbid"
