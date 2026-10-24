"""Pure cohort rules (#65 B; spec §3 structured course; ADR-0005 D4, D6).
No database access.

**Where cohorts live (#65 E2).** A cohort (``course_cohorts``) belongs to
this module; its sessions are the occurrences of one ``course`` series in
the ``scheduling`` module, which points at the cohort with
``class_series.cohort_id`` / ``class_sessions.cohort_id``. The cohort
routes call ``app.workflows.cohorts``, which composes ``courses.service``
and ``scheduling.service`` in one Unit of Work. ``scheduling`` may call
``courses.service`` (the cohort lock, the cohort names); ``courses`` never
imports ``scheduling``, so there is no import cycle.

**The dates (#65 E3).** A cohort's dates are one weekly ``course`` series:
the session length is the snapshot's ``session_minutes``; the caller gives
the start date, the start time and the weekdays and interval; ``COUNT`` is
derived (:func:`derive_occurrence_count`) so that the series has the
template's ``session_count`` occurrences outside the closures known at
creation (ADR-0005 D6: the center's calendar is known when a course is
sold). A closure added later skips a session with no automatic makeup; a
closure removed later can restore one (#232). The 12-week horizon and the
nightly job are unchanged (D4).

**One subject (#65 E4).** Every session of a cohort has the cohort's
``subject_id``, one of the snapshot's subjects; the tutor's qualification is
checked against it.

**Lock order (#65 G1):** students → zone → template ``FOR SHARE`` (only in
the cohort create) → group **or cohort** ``FOR UPDATE`` → series ``FOR
UPDATE`` → sessions by id → offer. ``courses`` never takes a scheduling
lock.

:func:`cohort_status` gives ``upcoming`` / ``in_progress`` / ``ended`` from
the series rule (not from materialized rows, so it holds beyond the
horizon); :func:`syllabus_of` maps a schema-version-1 snapshot to the
response shape.
"""

import uuid
from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Final

from app.modules.courses.models import SESSION_COUNT_MAX, SNAPSHOT_SCHEMA_VERSION
from app.modules.courses.schemas import (
    CohortStatus,
    CohortSyllabus,
    CourseCompletion,
    CourseTemplateAssessment,
    CourseTemplateModule,
)

OCCURRENCES_MAX: Final = SESSION_COUNT_MAX
"""ADR-0005 D4: a series has at most 200 occurrences (``COUNT`` 1-200)."""


class TooManyOccurrencesError(ValueError):
    """The rule's first 200 dates hold fewer than ``session_count`` dates
    outside the closures (#65 B1): ``422 too_many_occurrences`` on
    ``recurrence``. ``field`` and ``reason`` follow the scheduling
    recurrence errors."""

    field: Final = "recurrence"
    reason: Final = "too_many_occurrences"

    def __init__(self) -> None:
        super().__init__(f"{self.field}: {self.reason}")


def derive_occurrence_count(
    local_dates: Sequence[date], closed_dates: Collection[date], session_count: int
) -> int:
    """The smallest N whose first N ``local_dates`` hold exactly
    ``session_count`` dates outside ``closed_dates`` (#65 B1, E3).

    ``local_dates`` are the rule's first (at most 200) occurrence dates, in
    order. Raises :class:`TooManyOccurrencesError` when fewer than
    ``session_count`` open dates exist among the first 200 (D4's ``COUNT``
    bound)."""
    if session_count < 1:
        raise ValueError("session_count must be at least 1")
    open_dates = 0
    for index, day in enumerate(local_dates[:OCCURRENCES_MAX], start=1):
        if day not in closed_dates:
            open_dates += 1
            if open_dates == session_count:
                return index
    raise TooManyOccurrencesError()


def cohort_status(
    first_start: datetime,
    last_start: datetime,
    now: datetime,
    *,
    session_minutes: int,
) -> CohortStatus:
    """The cohort status (#65 B2): ``upcoming`` while ``now`` is before the
    first occurrence's start; ``ended`` once ``now`` reaches the end of the
    last occurrence (``last_start`` plus ``session_minutes``); otherwise
    ``in_progress``. The instants come from the series rule."""
    if now < first_start:
        return "upcoming"
    if now >= last_start + timedelta(minutes=session_minutes):
        return "ended"
    return "in_progress"


def _uuid(value: Any) -> uuid.UUID | None:
    return None if value is None else uuid.UUID(str(value))


def syllabus_of(
    content: Mapping[str, Any], *, schema_version: int = SNAPSHOT_SCHEMA_VERSION
) -> CohortSyllabus:
    """The syllabus of a snapshot's content (#65 B3): its modules and
    assessments in position order and its completion criteria. Only schema
    version 1 (#64 C2) is known; any other raises :class:`ValueError` (a
    guard for later template changes)."""
    if schema_version != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(f"unknown snapshot schema version {schema_version}")
    modules = sorted(content["modules"], key=lambda item: item["position"])
    assessments = sorted(content["assessments"], key=lambda item: item["position"])
    completion = content["completion"]
    return CohortSyllabus(
        modules=[
            CourseTemplateModule(
                id=uuid.UUID(item["id"]),
                position=item["position"],
                title=item["title"],
                description=item["description"],
                subject_id=_uuid(item["subject_id"]),
                planned_sessions=item["planned_sessions"],
                is_required=item["is_required"],
                materials=item["materials"],
            )
            for item in modules
        ],
        assessments=[
            CourseTemplateAssessment(
                id=uuid.UUID(item["id"]),
                position=item["position"],
                title=item["title"],
                module_id=_uuid(item["module_id"]),
                is_required=item["is_required"],
                min_score_percent=item["min_score_percent"],
            )
            for item in assessments
        ],
        completion=CourseCompletion(
            min_attendance_percent=completion["min_attendance_percent"],
            required_module_ids=[
                uuid.UUID(i) for i in completion["required_module_ids"]
            ],
            required_assessment_ids=[
                uuid.UUID(i) for i in completion["required_assessment_ids"]
            ],
        ),
    )


def snapshot_subject_ids(content: Mapping[str, Any]) -> tuple[uuid.UUID, ...]:
    """The subject ids a snapshot froze, in its order (#65 E4)."""
    return tuple(uuid.UUID(item["id"]) for item in content["subjects"])
