"""Course template rules and snapshot content (#64 C1-C3), pure."""

import json
import uuid
from typing import Any

import pytest

from app.modules.courses.schemas import (
    CourseTemplateAssessment,
    CourseTemplateModule,
    CourseTemplateWrite,
)
from app.modules.courses.templates import (
    SnapshotSubject,
    TemplateContent,
    build_snapshot,
    completion_of,
    validate_template,
)

MATH = uuid.UUID("01900000-0000-7000-8000-000000000001")
PHYSICS = uuid.UUID("01900000-0000-7000-8000-000000000002")
OTHER = uuid.UUID("01900000-0000-7000-8000-000000000003")


def module(title: str = "Módulo", **values: Any) -> dict[str, Any]:
    return {"title": title, "is_required": True, **values}


def assessment(title: str = "Examen", **values: Any) -> dict[str, Any]:
    return {"title": title, "is_required": True, **values}


def document(**values: Any) -> CourseTemplateWrite:
    body: dict[str, Any] = {
        "name": "Curso",
        "subject_ids": [MATH],
        "session_count": 10,
        "session_minutes": 90,
        "modules": [module("Uno"), module("Dos")],
        "assessments": [],
        **values,
    }
    return CourseTemplateWrite.model_validate(body)


def codes(body: CourseTemplateWrite) -> list[tuple[list[Any], str]]:
    return [(error["path"], error["code"]) for error in validate_template(body)]


# --- C1: validation ------------------------------------------------------------------


def test_a_minimal_template_without_criteria_is_valid():
    assert validate_template(document()) == []


def test_a_full_template_is_valid():
    body = document(
        subject_ids=[MATH, PHYSICS],
        min_attendance_percent=80,
        modules=[
            module("Uno", subject_id=MATH, planned_sessions=4),
            module("Dos", subject_id=PHYSICS, planned_sessions=6, is_required=False),
        ],
        assessments=[
            assessment("Parcial", module_index=1, min_score_percent=60),
            assessment("Final", module_index=None),
        ],
    )

    assert validate_template(body) == []


def test_no_subject_is_too_short():
    assert codes(document(subject_ids=[])) == [(["body", "subject_ids"], "too_short")]


def test_more_than_ten_subjects_is_too_long():
    # The schema caps the list too; the rule holds on its own.
    body = document()
    body.subject_ids = [uuid.uuid4() for _ in range(11)]

    assert codes(body) == [(["body", "subject_ids"], "too_long")]


def test_ten_subjects_are_valid():
    ids = [uuid.uuid4() for _ in range(10)]

    assert validate_template(document(subject_ids=ids)) == []


def test_a_repeated_subject_is_reported_at_its_second_index():
    assert codes(document(subject_ids=[MATH, PHYSICS, MATH])) == [
        (["body", "subject_ids", 2], "duplicate_subject")
    ]


def test_a_template_needs_a_module():
    assert codes(document(modules=[])) == [(["body", "modules"], "too_short")]


def test_more_than_fifty_modules_is_too_long():
    body = document()
    body.modules = document(modules=[module(f"M{i}") for i in range(50)]).modules + [
        document(modules=[module("M50")]).modules[0]
    ]

    assert codes(body) == [(["body", "modules"], "too_long")]


def test_module_titles_are_unique_ignoring_case():
    body = document(modules=[module("Álgebra"), module("Geometría"), module("álgebra")])

    assert codes(body) == [(["body", "modules", 2, "title"], "duplicate_title")]


def test_a_module_subject_must_be_a_template_subject():
    body = document(
        modules=[module("Uno", subject_id=MATH), module("Dos", subject_id=OTHER)]
    )

    assert codes(body) == [
        (["body", "modules", 1, "subject_id"], "module_subject_not_in_template")
    ]


def test_planned_sessions_may_reach_the_total():
    body = document(
        modules=[module("Uno", planned_sessions=4), module("Dos", planned_sessions=6)]
    )

    assert validate_template(body) == []


def test_planned_sessions_above_the_total_are_rejected_on_modules():
    body = document(
        modules=[
            module("Uno", planned_sessions=4),
            module("Dos"),
            module("Tres", planned_sessions=7),
        ]
    )

    assert codes(body) == [(["body", "modules"], "planned_sessions_exceed_total")]


def test_thirty_assessments_are_valid_and_more_are_too_long():
    thirty = [assessment(f"E{i}") for i in range(30)]
    assert validate_template(document(assessments=thirty)) == []

    body = document(assessments=thirty)
    body.assessments = [
        *body.assessments,
        document(assessments=[assessment("E30")]).assessments[0],
    ]
    assert codes(body) == [(["body", "assessments"], "too_long")]


def test_assessment_titles_are_unique_ignoring_case():
    body = document(assessments=[assessment("Final"), assessment("FINAL")])

    assert codes(body) == [(["body", "assessments", 1, "title"], "duplicate_title")]


@pytest.mark.parametrize("module_index", [2, 5, -1])
def test_a_module_index_outside_the_body_is_invalid(module_index):
    body = document(assessments=[assessment("Final", module_index=module_index)])

    assert codes(body) == [
        (["body", "assessments", 0, "module_index"], "module_index_invalid")
    ]


def test_a_module_index_inside_the_body_is_valid():
    body = document(
        assessments=[assessment("A", module_index=0), assessment("B", module_index=1)]
    )

    assert validate_template(body) == []


def test_an_item_id_repeated_in_a_list_is_item_unknown():
    kept = uuid.uuid4()
    body = document(
        modules=[module("Uno", id=kept), module("Dos", id=kept)],
        assessments=[assessment("A", id=kept), assessment("B", id=kept)],
    )

    assert codes(body) == [
        (["body", "modules", 1, "id"], "item_unknown"),
        (["body", "assessments", 1, "id"], "item_unknown"),
    ]


def test_every_violation_is_reported_together():
    body = document(
        subject_ids=[MATH, MATH],
        modules=[module("Uno", subject_id=OTHER, planned_sessions=11), module("uno")],
        assessments=[assessment("X", module_index=9)],
    )

    assert codes(body) == [
        (["body", "subject_ids", 1], "duplicate_subject"),
        (["body", "modules", 1, "title"], "duplicate_title"),
        (["body", "modules", 0, "subject_id"], "module_subject_not_in_template"),
        (["body", "modules"], "planned_sessions_exceed_total"),
        (["body", "assessments", 0, "module_index"], "module_index_invalid"),
    ]


def test_every_error_has_a_message():
    body = document(subject_ids=[])

    [error] = validate_template(body)
    assert error["message"]


# --- The schema's bounds ---------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        {"session_count": 0},
        {"session_count": 201},
        {"session_minutes": 14},
        {"session_minutes": 481},
        {"min_attendance_percent": 0},
        {"min_attendance_percent": 101},
        {"name": "   "},
        {"name": "x" * 101},
        {"modules": [module("x" * 201)]},
        {"modules": [module("Uno", planned_sessions=0)]},
        {"modules": [module("Uno", materials="m" * 2001)]},
        {"assessments": [assessment("A", min_score_percent=101)]},
        {"color": "blue"},
    ],
)
def test_the_schema_rejects_values_outside_the_bounds(values):
    with pytest.raises(ValueError):
        document(**values)


def test_blank_optional_texts_are_null_and_texts_are_trimmed():
    body = document(
        name="  Curso  ",
        description="   ",
        modules=[module("  Uno  ", materials="  Libro 1  ")],
    )

    assert (body.name, body.description) == ("Curso", None)
    assert (body.modules[0].title, body.modules[0].materials) == ("Uno", "Libro 1")


# --- C2: snapshot content ------------------------------------------------------------

M1, M2, M3 = (uuid.UUID(f"01900000-0000-7000-8000-00000000010{i}") for i in (1, 2, 3))
A1, A2 = (uuid.UUID(f"01900000-0000-7000-8000-00000000020{i}") for i in (1, 2))
LEVEL = uuid.UUID("01900000-0000-7000-8000-000000000300")


def stored_module(
    item_id: uuid.UUID, position: int, **values: Any
) -> CourseTemplateModule:
    fields: dict[str, Any] = {
        "id": item_id,
        "position": position,
        "title": f"Módulo {position}",
        "description": None,
        "subject_id": None,
        "planned_sessions": None,
        "is_required": True,
        "materials": None,
        **values,
    }
    return CourseTemplateModule(**fields)


def stored_assessment(
    item_id: uuid.UUID, position: int, **values: Any
) -> CourseTemplateAssessment:
    fields: dict[str, Any] = {
        "id": item_id,
        "position": position,
        "title": f"Evaluación {position}",
        "module_id": None,
        "is_required": True,
        "min_score_percent": None,
        **values,
    }
    return CourseTemplateAssessment(**fields)


def content(**values: Any) -> TemplateContent:
    fields: dict[str, Any] = {
        "name": "Curso",
        "description": "Descripción",
        "session_count": 10,
        "session_minutes": 90,
        "min_attendance_percent": 80,
        "subjects": [SnapshotSubject(MATH, "Matemáticas", LEVEL, "Secundaria")],
        # Out of order on purpose: the snapshot sorts by position.
        "modules": [
            stored_module(M3, 3, is_required=False),
            stored_module(
                M1, 1, subject_id=MATH, planned_sessions=4, materials="Libro"
            ),
            stored_module(M2, 2),
        ],
        "assessments": [
            stored_assessment(A2, 2, is_required=False),
            stored_assessment(A1, 1, module_id=M2, min_score_percent=60),
        ],
        **values,
    }
    return TemplateContent(**fields)


def test_the_snapshot_holds_exactly_the_schema_1_fields():
    snapshot = build_snapshot(content())

    assert snapshot == {
        "name": "Curso",
        "description": "Descripción",
        "session_count": 10,
        "session_minutes": 90,
        "subjects": [
            {
                "id": str(MATH),
                "name": "Matemáticas",
                "level_id": str(LEVEL),
                "level_name": "Secundaria",
            }
        ],
        "modules": [
            {
                "id": str(M1),
                "position": 1,
                "title": "Módulo 1",
                "description": None,
                "subject_id": str(MATH),
                "planned_sessions": 4,
                "is_required": True,
                "materials": "Libro",
            },
            {
                "id": str(M2),
                "position": 2,
                "title": "Módulo 2",
                "description": None,
                "subject_id": None,
                "planned_sessions": None,
                "is_required": True,
                "materials": None,
            },
            {
                "id": str(M3),
                "position": 3,
                "title": "Módulo 3",
                "description": None,
                "subject_id": None,
                "planned_sessions": None,
                "is_required": False,
                "materials": None,
            },
        ],
        "assessments": [
            {
                "id": str(A1),
                "position": 1,
                "title": "Evaluación 1",
                "module_id": str(M2),
                "is_required": True,
                "min_score_percent": 60,
            },
            {
                "id": str(A2),
                "position": 2,
                "title": "Evaluación 2",
                "module_id": None,
                "is_required": False,
                "min_score_percent": None,
            },
        ],
        "completion": {
            "min_attendance_percent": 80,
            "required_module_ids": [str(M1), str(M2)],
            "required_assessment_ids": [str(A1)],
        },
    }


def test_the_snapshot_is_deterministic():
    first = json.dumps(build_snapshot(content()), sort_keys=True)
    shuffled = content(
        modules=list(reversed(content().modules)),
        assessments=list(reversed(content().assessments)),
    )

    assert json.dumps(build_snapshot(content()), sort_keys=True) == first
    assert json.dumps(build_snapshot(shuffled), sort_keys=True) == first


def test_a_template_without_criteria_has_an_empty_completion_block():
    snapshot = build_snapshot(
        content(
            min_attendance_percent=None,
            modules=[stored_module(M1, 1, is_required=False)],
            assessments=[],
        )
    )

    assert snapshot["completion"] == {
        "min_attendance_percent": None,
        "required_module_ids": [],
        "required_assessment_ids": [],
    }


def test_completion_lists_required_items_in_position_order():
    completion = completion_of(
        75,
        [
            stored_module(M2, 2),
            stored_module(M1, 1),
            stored_module(M3, 3, is_required=False),
        ],
        [stored_assessment(A2, 2), stored_assessment(A1, 1)],
    )

    assert completion.min_attendance_percent == 75
    assert completion.required_module_ids == [M1, M2]
    assert completion.required_assessment_ids == [A1, A2]
