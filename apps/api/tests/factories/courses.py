"""Course factories (#65): a cohort with its template and snapshot.

:func:`persist_course_cohort` writes the rows directly (flush, never
commit): a one-module template for ``subject_id``, its version-1 snapshot
and a cohort on it. Use it where a ``course`` session or series needs a
cohort (``class_sessions.cohort_id``, #65 A4); the cohort routes are tested
through the API.
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.courses.models import (
    CourseCohort,
    CourseTemplate,
    CourseTemplateSnapshot,
    CourseTemplateSubject,
)


async def persist_course_template(
    session: AsyncSession,
    *,
    subject_id: uuid.UUID,
    session_count: int = 10,
    session_minutes: int = 60,
) -> CourseTemplate:
    """An active template (no modules: a test may snapshot it) linked to
    ``subject_id``, with a unique name."""
    template = CourseTemplate(
        name=f"Curso {uuid.uuid4().hex[:8]}",
        session_count=session_count,
        session_minutes=session_minutes,
    )
    session.add(template)
    await session.flush()
    session.add(CourseTemplateSubject(template_id=template.id, subject_id=subject_id))
    await session.flush()
    return template


async def persist_course_cohort(
    session: AsyncSession,
    *,
    subject_id: uuid.UUID,
    max_students: int = 12,
    session_count: int = 10,
    session_minutes: int = 60,
    assessments: Sequence[dict[str, Any]] = (),
) -> CourseCohort:
    """A cohort of a new template (``session_count`` × ``session_minutes``)
    on ``subject_id``, with unique names. ``assessments`` (``id``,
    ``title``, optional ``is_required`` and ``min_score_percent``) go into
    the snapshot only (#80: evaluations read them from there)."""
    suffix = uuid.uuid4().hex[:8]
    template = await persist_course_template(
        session,
        subject_id=subject_id,
        session_count=session_count,
        session_minutes=session_minutes,
    )
    snapshot = CourseTemplateSnapshot(
        template_id=template.id,
        template_version=template.version,
        content={
            "name": template.name,
            "description": None,
            "session_count": session_count,
            "session_minutes": session_minutes,
            "subjects": [
                {
                    "id": str(subject_id),
                    "name": "Materia",
                    "level_id": str(uuid.uuid4()),
                    "level_name": "Nivel",
                }
            ],
            "modules": [],
            "assessments": [
                {
                    "id": str(item["id"]),
                    "position": position,
                    "title": item["title"],
                    "module_id": None,
                    "is_required": item.get("is_required", True),
                    "min_score_percent": item.get("min_score_percent"),
                }
                for position, item in enumerate(assessments, start=1)
            ],
            "completion": {
                "min_attendance_percent": None,
                "required_module_ids": [],
                "required_assessment_ids": [],
            },
        },
    )
    session.add(snapshot)
    await session.flush()
    cohort = CourseCohort(
        name=f"Cohorte {suffix}",
        template_id=template.id,
        template_snapshot_id=snapshot.id,
        subject_id=subject_id,
        max_students=max_students,
    )
    session.add(cohort)
    await session.flush()
    return cohort
