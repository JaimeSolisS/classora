"""The pure course completion rules (#85 A1-A3,
``app.modules.courses.completion``)."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.modules.courses.completion import (
    CountedEvaluation,
    Criteria,
    Participation,
    UnsupportedSnapshotSchemaError,
    assessment_outcome,
    attendance_met,
    attendance_percent,
    criteria_of,
    evaluate_student,
    is_final,
)

M_EVIDENCED = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
M_COVERED = uuid.UUID("00000000-0000-4000-8000-0000000000a2")
M_OPTIONAL = uuid.UUID("00000000-0000-4000-8000-0000000000a3")
A_MODULE = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
A_COURSE = uuid.UUID("00000000-0000-4000-8000-0000000000b2")
A_OPTIONAL = uuid.UUID("00000000-0000-4000-8000-0000000000b3")
A_OF_OPTIONAL_MODULE = uuid.UUID("00000000-0000-4000-8000-0000000000b4")


def _module(module_id: uuid.UUID, position: int, *, required: bool) -> dict[str, Any]:
    return {
        "id": str(module_id),
        "position": position,
        "title": f"Módulo {position}",
        "description": None,
        "subject_id": None,
        "planned_sessions": None,
        "is_required": required,
        "materials": None,
    }


def _assessment(
    assessment_id: uuid.UUID,
    position: int,
    *,
    module: uuid.UUID | None = None,
    required: bool = True,
    minimum: int | None = None,
) -> dict[str, Any]:
    return {
        "id": str(assessment_id),
        "position": position,
        "title": f"Evaluación {position}",
        "module_id": None if module is None else str(module),
        "is_required": required,
        "min_score_percent": minimum,
    }


def content(
    *,
    min_attendance: int | None = None,
    modules: list[dict[str, Any]] | None = None,
    assessments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A schema-version-1 snapshot; positions may come unsorted."""
    modules = modules or []
    assessments = assessments or []
    return {
        "name": "Curso",
        "description": None,
        "session_count": 10,
        "session_minutes": 60,
        "subjects": [],
        "modules": modules,
        "assessments": assessments,
        "completion": {
            "min_attendance_percent": min_attendance,
            "required_module_ids": [m["id"] for m in modules if m["is_required"]],
            "required_assessment_ids": [
                a["id"] for a in assessments if a["is_required"]
            ],
        },
    }


def criteria(**values: Any) -> Criteria:
    return criteria_of(content(**values), schema_version=1)


FULL = dict(
    min_attendance=80,
    # Out of position order on purpose.
    modules=[
        _module(M_OPTIONAL, 3, required=False),
        _module(M_EVIDENCED, 1, required=True),
        _module(M_COVERED, 2, required=True),
    ],
    assessments=[
        _assessment(A_OPTIONAL, 3, required=False, minimum=90),
        _assessment(A_MODULE, 1, module=M_EVIDENCED, minimum=70),
        _assessment(A_COURSE, 2),
        _assessment(A_OF_OPTIONAL_MODULE, 4, module=M_OPTIONAL, minimum=50),
    ],
)


ALL_PASSED = {A_MODULE: "70", A_COURSE: "1", A_OF_OPTIONAL_MODULE: "50"}


def counted(score: str, attempts: int = 1) -> CountedEvaluation:
    return CountedEvaluation(
        evaluation_id=uuid.uuid4(),
        score_percent=Decimal(score),
        evaluated_on=date(2031, 3, 1),
        attempts=attempts,
    )


def held(n: int, attended: int, **extra: int) -> Participation:
    return Participation(held=n, attended=attended, absent=n - attended, **extra)


# --- A1: the inputs ------------------------------------------------------------------


def test_the_criteria_follow_the_snapshot_in_position_order():
    found = criteria(**FULL)
    assert found.min_attendance_percent == 80
    assert [m.id for m in found.modules] == [M_EVIDENCED, M_COVERED, M_OPTIONAL]
    assert [m.id for m in found.required_modules] == [M_EVIDENCED, M_COVERED]
    assert [a.id for a in found.assessments] == [
        A_MODULE,
        A_COURSE,
        A_OPTIONAL,
        A_OF_OPTIONAL_MODULE,
    ]
    assert found.assessments[0].module_id == M_EVIDENCED
    assert found.assessments[0].min_score_percent == 70
    assert found.assessments[1].min_score_percent is None


@pytest.mark.parametrize("version", [0, 2, 99])
def test_an_unknown_schema_version_is_a_programming_error(version):
    with pytest.raises(UnsupportedSnapshotSchemaError):
        criteria_of(content(), schema_version=version)


# --- E5: final ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "awaiting", "expected"),
    [
        ("upcoming", 0, False),
        ("in_progress", 0, False),
        ("ended", 1, False),
        ("ended", 0, True),
    ],
)
def test_the_course_is_final_once_ended_and_closed_out(status, awaiting, expected):
    assert (
        is_final(status, Participation(held=3, awaiting_closeout=awaiting)) is expected
    )


# --- E3: attendance -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("attended", "total", "minimum", "met"),
    [
        (8, 10, 80, True),  # the exact boundary
        (7, 10, 80, False),
        (2, 3, 66, True),  # 66.67 %
        (2, 3, 67, False),  # 66.67 % rounds to 67 but is below it
        (0, 0, 0, False),  # nothing held is never met
        (0, 1, 0, True),
        (1, 1, 100, True),
    ],
)
def test_attendance_is_compared_in_integers(attended, total, minimum, met):
    assert attendance_met(attended, total, minimum) is met


@pytest.mark.parametrize(
    ("attended", "total", "shown"),
    [
        (1, 3, Decimal("33.33")),
        (2, 3, Decimal("66.67")),
        (8, 10, Decimal("80.00")),
        (1, 8, Decimal("12.50")),
        (0, 0, None),
    ],
)
def test_the_shown_percentage_is_half_up_to_two_places(attended, total, shown):
    found = attendance_percent(attended, total)
    assert found == shown
    if found is not None:
        assert found.as_tuple().exponent == -2


def test_a_rounded_percentage_never_decides():
    # 79.995 % would show as 80.00 but isn't 80 %.
    result = evaluate_student(
        criteria(min_attendance=80),
        held(20000, 15999),
        {},
        final=True,
    )
    assert result.attendance.attendance_percent == Decimal("80.00")
    assert result.attendance.status == "not_met"
    assert result.status == "not_completed"


@pytest.mark.parametrize(
    ("facts", "final", "status"),
    [
        (held(10, 8), True, "met"),
        (held(10, 7), True, "not_met"),
        (held(10, 7), False, "pending"),
        (held(10, 10), False, "pending"),
        (Participation(), True, "not_met"),  # held = 0
        (Participation(), False, "pending"),
    ],
)
def test_the_attendance_criterion(facts, final, status):
    result = evaluate_student(criteria(min_attendance=80), facts, {}, final=final)
    assert result.attendance.status == status
    assert result.attendance.min_percent == 80


def test_late_counts_as_attended():
    # The caller counts present and late as attended: 6 present + 2 late.
    facts = Participation(held=10, attended=8, absent=2)
    result = evaluate_student(criteria(min_attendance=80), facts, {}, final=True)
    assert result.attendance.status == "met"
    assert result.attendance.attendance_percent == Decimal("80.00")


def test_attendance_without_a_minimum_is_not_required():
    facts = Participation(held=4, attended=1, absent=3, remaining=2)
    result = evaluate_student(criteria(), facts, {}, final=True)
    attendance = result.attendance
    assert attendance.status == "not_required"
    assert attendance.min_percent is None
    assert (
        attendance.held,
        attendance.attended,
        attendance.absent,
        attendance.remaining,
        attendance.awaiting_closeout,
    ) == (4, 1, 3, 2, 0)
    assert attendance.attendance_percent == Decimal("25.00")
    assert result.status == "completed"


# --- E4: assessments ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("minimum", "score", "outcome"),
    [
        (70, "69.99", "failed"),
        (70, "70.00", "passed"),
        (70, "100.00", "passed"),
        (None, "0.00", "passed"),
        (70, None, "not_evaluated"),
        (None, None, "not_evaluated"),
        (0, "0.00", "passed"),
    ],
)
def test_an_assessment_outcome(minimum, score, outcome):
    found = None if score is None else counted(score)
    assert assessment_outcome(minimum, found) == outcome


def test_the_assessment_items_follow_the_snapshot():
    result = evaluate_student(
        criteria(**FULL),
        Participation(),
        {A_MODULE: counted("71.50", attempts=3), A_OPTIONAL: counted("10.00")},
        final=False,
    )
    items = result.assessments.items
    assert [i.assessment_id for i in items] == [
        A_MODULE,
        A_COURSE,
        A_OPTIONAL,
        A_OF_OPTIONAL_MODULE,
    ]
    first = items[0]
    assert (first.status, first.score_percent, first.attempts) == (
        "passed",
        Decimal("71.50"),
        3,
    )
    assert first.evaluated_on == date(2031, 3, 1)
    assert (first.title, first.is_required, first.min_score_percent) == (
        "Evaluación 1",
        True,
        70,
    )
    second = items[1]
    assert (second.status, second.score_percent, second.evaluated_on) == (
        "not_evaluated",
        None,
        None,
    )
    assert second.attempts == 0
    assert items[2].status == "failed"
    assert items[2].is_required is False


@pytest.mark.parametrize(
    ("scores", "final", "status"),
    [
        ({}, False, "pending"),
        ({}, True, "not_met"),
        ({A_MODULE: "70.00"}, False, "pending"),
        (
            {A_MODULE: "70.00", A_COURSE: "1.00", A_OF_OPTIONAL_MODULE: "50"},
            False,
            "met",
        ),
        (
            {A_MODULE: "70.00", A_COURSE: "1.00", A_OF_OPTIONAL_MODULE: "50"},
            True,
            "met",
        ),
        # A failed required assessment: pending before the end (a retake is
        # possible), not met once final.
        ({A_MODULE: "69.99", A_COURSE: "1.00"}, False, "pending"),
        ({A_MODULE: "69.99", A_COURSE: "1.00"}, True, "not_met"),
        # An optional failed assessment doesn't block.
        ({**ALL_PASSED, A_OPTIONAL: "5"}, True, "met"),
    ],
)
def test_the_assessments_criterion(scores, final, status):
    evaluations = {k: counted(v) for k, v in scores.items()}
    result = evaluate_student(
        criteria(**FULL), Participation(), evaluations, final=final
    )
    assert result.assessments.status == status


def test_no_required_assessment_is_not_required():
    result = evaluate_student(
        criteria(assessments=[_assessment(A_OPTIONAL, 1, required=False)]),
        Participation(),
        {A_OPTIONAL: counted("0.00")},
        final=True,
    )
    assert result.assessments.status == "not_required"
    assert result.assessments.items[0].status == "passed"


# --- E2: modules ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scores", "final", "evidenced", "covered"),
    [
        ({}, False, "pending", "pending"),
        ({}, True, "not_met", "met"),
        ({A_MODULE: "70.00"}, False, "met", "pending"),
        ({A_MODULE: "70.00"}, True, "met", "met"),
        ({A_MODULE: "50.00"}, False, "pending", "pending"),
        ({A_MODULE: "50.00"}, True, "not_met", "met"),
        # The course-wide assessment is no evidence for a module.
        ({A_COURSE: "100.00"}, True, "not_met", "met"),
    ],
)
def test_each_required_module(scores, final, evidenced, covered):
    evaluations = {k: counted(v) for k, v in scores.items()}
    result = evaluate_student(
        criteria(**FULL), Participation(), evaluations, final=final
    )
    items = result.modules.items
    # Only required modules, in position order.
    assert [(i.module_id, i.basis) for i in items] == [
        (M_EVIDENCED, "assessments"),
        (M_COVERED, "course_covered"),
    ]
    assert [i.title for i in items] == ["Módulo 1", "Módulo 2"]
    assert (items[0].status, items[1].status) == (evidenced, covered)
    expected = (
        "met" if evidenced == covered == "met" else ("not_met" if final else "pending")
    )
    assert result.modules.status == expected


def test_a_module_whose_only_assessment_is_optional_is_covered_by_the_course():
    result = evaluate_student(
        criteria(
            modules=[_module(M_EVIDENCED, 1, required=True)],
            assessments=[
                _assessment(A_MODULE, 1, module=M_EVIDENCED, required=False, minimum=99)
            ],
        ),
        Participation(),
        {A_MODULE: counted("10.00")},
        final=True,
    )
    [item] = result.modules.items
    assert (item.status, item.basis) == ("met", "course_covered")
    assert result.status == "completed"


def test_an_assessment_of_a_non_required_module_counts_only_under_assessments():
    result = evaluate_student(
        criteria(**FULL),
        held(10, 10),
        {A_MODULE: counted("70"), A_COURSE: counted("1")},
        final=True,
    )
    # A_OF_OPTIONAL_MODULE is required and not evaluated: the assessments
    # criterion fails; the optional module isn't listed at all.
    assert result.assessments.status == "not_met"
    assert M_OPTIONAL not in {i.module_id for i in result.modules.items}
    assert result.modules.status == "met"
    assert result.status == "not_completed"


def test_no_required_module_is_not_required():
    result = evaluate_student(
        criteria(modules=[_module(M_OPTIONAL, 1, required=False)]),
        Participation(),
        {},
        final=False,
    )
    assert result.modules.status == "not_required"
    assert result.modules.items == ()


# --- E5: overall ----------------------------------------------------------------------


def test_no_criteria_completes_once_final():
    before = evaluate_student(criteria(), Participation(), {}, final=False)
    after = evaluate_student(criteria(), Participation(), {}, final=True)
    assert (before.status, before.final) == ("in_progress", False)
    assert (after.status, after.final) == ("completed", True)
    for result in (before, after):
        assert result.attendance.status == "not_required"
        assert result.assessments.status == "not_required"
        assert result.modules.status == "not_required"


@pytest.mark.parametrize(
    ("facts", "scores", "final", "status"),
    [
        (held(10, 10), ALL_PASSED, False, "in_progress"),
        (held(10, 8), ALL_PASSED, True, "completed"),
        (held(10, 7), ALL_PASSED, True, "not_completed"),
        (held(10, 10), {**ALL_PASSED, A_MODULE: "69.99"}, True, "not_completed"),
        (
            held(10, 10),
            {A_COURSE: "1", A_OF_OPTIONAL_MODULE: "50"},
            True,
            "not_completed",
        ),
    ],
)
def test_the_overall_status(facts, scores, final, status):
    evaluations = {k: counted(v) for k, v in scores.items()}
    result = evaluate_student(criteria(**FULL), facts, evaluations, final=final)
    assert result.status == status
    assert result.final is final
