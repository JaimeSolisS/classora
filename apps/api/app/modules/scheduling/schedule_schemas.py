"""The role-scoped schedule bodies (#67 B2, B4, C3, C4).

Every model is a **closed allow-list** (``extra="forbid"`` on the output
models too; ``tests/integration/test_family_schedule.py`` and
``test_tutor_schedule.py`` compare their JSON keys to the expected sets):

- a **tutor** item has its students but no markers, override data,
  reassignment flags, cancellation reason or offer details (#67 B4);
- a **family** item has the tutor's display name only, the classroom's name
  only, the path student's own attendance status (#76 E4), and never other
  participants, markers, overrides, reassignment flags, offer data or the
  cancellation reason (#67 C3, E4).

Times are instants with an offset (ADR-0004).
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.scheduling.schemas import SessionKindCode

type TutorScheduleState = Literal[
    "offered", "confirmed", "completed", "cancelled", "skipped_closure"
]
"""The states a tutor's schedule shows (``states.is_tutor_visible``)."""
type FamilyScheduleState = Literal[
    "confirmed", "completed", "cancelled", "skipped_closure"
]
"""The states a family schedule shows (``states.is_family_visible``)."""
type FamilyRelation = Literal["self", "guardian"]
type ScheduleAccessCode = Literal["allowed", "consent_required"]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScheduleRangeQuery(_Closed):
    """The query of both schedule reads (#67 B2, C2; the #57 A1 rules):
    instants with an offset; a session matches when it overlaps ``[from,
    to)``. No filters; unknown parameters are ``422``."""

    from_: datetime = Field(
        alias="from", description="An instant with an offset (RFC 3339)."
    )
    to: datetime = Field(
        description=(
            "An instant with an offset (RFC 3339), after `from` and at most"
            " 45 days later."
        )
    )


class ScheduleSubject(_Closed):
    """The session's subject with its level's name."""

    id: uuid.UUID
    name: str
    education_level_name: str


class TutorScheduleClassroom(_Closed):
    """The session's classroom."""

    id: uuid.UUID
    name: str


class TutorScheduleParticipant(_Closed):
    """A student of the tutor's session."""

    student_id: uuid.UUID
    display_name: str


class TutorScheduleItem(_Closed):
    """One session on a tutor's schedule (#67 B4)."""

    id: uuid.UUID
    kind: SessionKindCode
    state: TutorScheduleState
    starts_at: datetime
    ends_at: datetime
    subject: ScheduleSubject
    classroom: TutorScheduleClassroom | None
    participants: list[TutorScheduleParticipant] = Field(
        description="The students, by display name."
    )


class TutorSchedule(_Closed):
    """The tutor's sessions overlapping the range, by ``starts_at`` then
    ``id``; at most 1000, with ``truncated`` true when more match."""

    items: list[TutorScheduleItem]
    truncated: bool


class FamilyScheduleTutor(_Closed):
    """Who teaches the session: the display name only (#67 E4)."""

    display_name: str


class FamilyScheduleClassroom(_Closed):
    """Where the session is: the classroom's name only."""

    name: str


class FamilyScheduleItem(_Closed):
    """One session on a student's schedule (#67 C3)."""

    id: uuid.UUID
    kind: SessionKindCode
    state: FamilyScheduleState
    starts_at: datetime
    ends_at: datetime
    subject: ScheduleSubject
    tutor: FamilyScheduleTutor | None
    classroom: FamilyScheduleClassroom | None
    attendance: Literal["present", "late", "absent"] | None = Field(
        description=(
            "This student's attendance in the session (#76 E4); `null` when"
            " not marked. Never other students', who marked it or corrections."
        )
    )


class FamilySchedule(_Closed):
    """The student's sessions overlapping the range, by ``starts_at`` then
    ``id``; at most 1000, with ``truncated`` true when more match."""

    student_id: uuid.UUID
    items: list[FamilyScheduleItem]
    truncated: bool


class FamilyScheduleStudent(_Closed):
    """A student whose schedule the caller may choose (#67 C4)."""

    student_id: uuid.UUID
    display_name: str
    relation: FamilyRelation = Field(
        description="`self`: the caller's own profile; `guardian`: a linked child."
    )
    is_minor: bool = Field(description="A minor today in the center's time zone.")
    schedule_access: ScheduleAccessCode = Field(
        description=(
            "`consent_required`: an adult who hasn't given this guardian"
            " academic access; their schedule answers 403"
            " student_consent_required."
        )
    )


class FamilyScheduleStudents(_Closed):
    """The caller's own profile and the students linked to their active
    guardian profile, by display name. Never a tutor's or staff member's
    other students."""

    items: list[FamilyScheduleStudent]
