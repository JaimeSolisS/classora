"""Pure course template rules (#64 C): document validation and the snapshot
content. No database access; the service adds what needs one (subjects in
the catalog, item ids of the stored template, the name).

:func:`validate_template` returns field errors with item indexes, for
example ``["body", "modules", 2, "title"]``:

- 1 to 10 subjects, none twice (``duplicate_subject``);
- 1 to 50 modules (a template always has one; there are no drafts), titles
  unique ignoring case (``duplicate_title``);
- a module's ``subject_id``, when set, is one of the template's subjects
  (``module_subject_not_in_template``);
- the set ``planned_sessions`` add up to at most ``session_count``
  (``planned_sessions_exceed_total``, on ``modules``);
- 0 to 30 assessments, titles unique ignoring case (``duplicate_title``),
  each ``module_index`` pointing at a module of the same body
  (``module_index_invalid``);
- an item ``id`` appears once per list (``item_unknown`` on the repeat).

A template without completion criteria (no minimum attendance, nothing
required) is valid. Counts outside the bounds are ``too_short`` /
``too_long`` (the schema's codes; the schema also caps the lists).

:func:`build_snapshot` builds the snapshot content of schema version 1;
:func:`completion_of` the derived completion criteria shared by the detail
and the snapshot.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.core.errors import FieldError
from app.modules.courses.models import (
    ASSESSMENTS_MAX,
    MODULES_MAX,
    MODULES_MIN,
    SUBJECTS_MAX,
    SUBJECTS_MIN,
)
from app.modules.courses.schemas import (
    CourseCompletion,
    CourseTemplateAssessment,
    CourseTemplateModule,
    CourseTemplateWrite,
)

type Path = list[str | int]


def _error(path: Path, code: str, message: str) -> FieldError:
    return {"path": ["body", *path], "code": code, "message": message}


def _count_errors(
    field: str, count: int, low: int, high: int, noun: str
) -> list[FieldError]:
    if count < low:
        return [_error([field], "too_short", f"Add at least {low} {noun}.")]
    if count > high:
        return [_error([field], "too_long", f"Add at most {high} {noun}.")]
    return []


def _duplicate_titles(field: str, titles: Iterable[str]) -> list[FieldError]:
    seen: set[str] = set()
    errors: list[FieldError] = []
    for index, title in enumerate(titles):
        key = title.casefold()
        if key in seen:
            errors.append(
                _error(
                    [field, index, "title"],
                    "duplicate_title",
                    "Another item of this list has the same title.",
                )
            )
        seen.add(key)
    return errors


def _repeated_ids(field: str, ids: Iterable[uuid.UUID | None]) -> list[FieldError]:
    seen: set[uuid.UUID] = set()
    errors: list[FieldError] = []
    for index, item_id in enumerate(ids):
        if item_id is None:
            continue
        if item_id in seen:
            errors.append(
                _error(
                    [field, index, "id"],
                    "item_unknown",
                    "This item is already in the list.",
                )
            )
        seen.add(item_id)
    return errors


def validate_template(body: CourseTemplateWrite) -> list[FieldError]:
    """Every rule violation of the document, in field order (empty: valid)."""
    errors: list[FieldError] = []

    errors += _count_errors(
        "subject_ids", len(body.subject_ids), SUBJECTS_MIN, SUBJECTS_MAX, "subjects"
    )
    seen_subjects: set[uuid.UUID] = set()
    for index, subject_id in enumerate(body.subject_ids):
        if subject_id in seen_subjects:
            errors.append(
                _error(
                    ["subject_ids", index],
                    "duplicate_subject",
                    "Choose each subject only once.",
                )
            )
        seen_subjects.add(subject_id)

    errors += _count_errors(
        "modules", len(body.modules), MODULES_MIN, MODULES_MAX, "modules"
    )
    errors += _repeated_ids("modules", (module.id for module in body.modules))
    errors += _duplicate_titles("modules", (module.title for module in body.modules))
    for index, module in enumerate(body.modules):
        if module.subject_id is not None and module.subject_id not in seen_subjects:
            errors.append(
                _error(
                    ["modules", index, "subject_id"],
                    "module_subject_not_in_template",
                    "Choose one of the template's subjects.",
                )
            )
    planned = sum(module.planned_sessions or 0 for module in body.modules)
    if planned > body.session_count:
        errors.append(
            _error(
                ["modules"],
                "planned_sessions_exceed_total",
                "The planned sessions of the modules exceed the number of sessions.",
            )
        )

    errors += _count_errors(
        "assessments", len(body.assessments), 0, ASSESSMENTS_MAX, "assessments"
    )
    errors += _repeated_ids("assessments", (item.id for item in body.assessments))
    errors += _duplicate_titles(
        "assessments", (item.title for item in body.assessments)
    )
    for index, assessment in enumerate(body.assessments):
        module_index = assessment.module_index
        if module_index is not None and not 0 <= module_index < len(body.modules):
            errors.append(
                _error(
                    ["assessments", index, "module_index"],
                    "module_index_invalid",
                    "Choose a module of this template.",
                )
            )
    return errors


# --- Snapshot content (schema version 1) ---------------------------------------------


@dataclass(frozen=True)
class SnapshotSubject:
    """A subject as the snapshot keeps it: its name and level at snapshot
    time."""

    id: uuid.UUID
    name: str
    level_id: uuid.UUID
    level_name: str


@dataclass(frozen=True)
class TemplateContent:
    """What a snapshot is built from: the template's fields, its subjects
    (in catalog order) and its modules and assessments (any order; they are
    sorted by position)."""

    name: str
    description: str | None
    session_count: int
    session_minutes: int
    min_attendance_percent: int | None
    subjects: Sequence[SnapshotSubject]
    modules: Sequence[CourseTemplateModule]
    assessments: Sequence[CourseTemplateAssessment]


def completion_of(
    min_attendance_percent: int | None,
    modules: Iterable[CourseTemplateModule],
    assessments: Iterable[CourseTemplateAssessment],
) -> CourseCompletion:
    """The completion criteria: the minimum attendance and the ids of the
    required modules and assessments, in position order."""
    return CourseCompletion(
        min_attendance_percent=min_attendance_percent,
        required_module_ids=[
            module.id
            for module in sorted(modules, key=lambda item: item.position)
            if module.is_required
        ],
        required_assessment_ids=[
            assessment.id
            for assessment in sorted(assessments, key=lambda item: item.position)
            if assessment.is_required
        ],
    )


def _id(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


def build_snapshot(template: TemplateContent) -> dict[str, Any]:
    """The snapshot content (schema version 1), JSON-ready: ids as strings,
    modules and assessments in position order, subjects as given. The same
    template always gives the same dict (``json.dumps(..., sort_keys=True)``
    is byte-identical)."""
    modules = sorted(template.modules, key=lambda item: item.position)
    assessments = sorted(template.assessments, key=lambda item: item.position)
    completion = completion_of(template.min_attendance_percent, modules, assessments)
    return {
        "name": template.name,
        "description": template.description,
        "session_count": template.session_count,
        "session_minutes": template.session_minutes,
        "subjects": [
            {
                "id": str(subject.id),
                "name": subject.name,
                "level_id": str(subject.level_id),
                "level_name": subject.level_name,
            }
            for subject in template.subjects
        ],
        "modules": [
            {
                "id": str(module.id),
                "position": module.position,
                "title": module.title,
                "description": module.description,
                "subject_id": _id(module.subject_id),
                "planned_sessions": module.planned_sessions,
                "is_required": module.is_required,
                "materials": module.materials,
            }
            for module in modules
        ],
        "assessments": [
            {
                "id": str(assessment.id),
                "position": assessment.position,
                "title": assessment.title,
                "module_id": _id(assessment.module_id),
                "is_required": assessment.is_required,
                "min_score_percent": assessment.min_score_percent,
            }
            for assessment in assessments
        ],
        "completion": {
            "min_attendance_percent": completion.min_attendance_percent,
            "required_module_ids": [str(i) for i in completion.required_module_ids],
            "required_assessment_ids": [
                str(i) for i in completion.required_assessment_ids
            ],
        },
    }
