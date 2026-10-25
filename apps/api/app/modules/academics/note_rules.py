"""Session note rules (#79 A3, C2, D1, E3, E4, E5). Pure functions, no I/O.

- :data:`BODY_MAX` / :data:`UNPUBLISH_REASON_MAX`: the text bounds (the
  database CHECKs repeat them).
- :data:`NOTABLE_STATES`: the session states a note may be written on
  (``confirmed`` at any time, ``completed``; E5). The states are compared
  as text; ``tests/unit/test_note_rules.py`` checks them against
  ``scheduling.states.SessionState`` (academics imports no scheduling).
- :func:`note_relation`: how the caller relates to one note (E3): its
  ``author`` (holding the ``tutor`` role), a tutor whose current
  assignments include the student (``assigned_tutor``), an
  ``academic_note.review`` holder (``reviewer``), or ``none`` (the ``404``
  of an unknown id). With several roles, the first that applies wins; a
  reviewer who is also the author keeps both (``is_reviewer``).
- :func:`write_block`: the ``403`` of a write (D1 step 4) for a caller who
  may read the note; :func:`state_block`: the ``409`` of a write against
  the note's visibility (D1 step 7). A ``can_*`` flag is "both ``None``".
- :func:`session_note_access`: the record policy of the session routes
  (``POST`` / ``GET /class-sessions/{id}/notes``): the session's
  **current** tutor while they hold the ``tutor`` role (an accepted #55
  substitute; never the original tutor after it), or a reviewer.
"""

import uuid
from enum import Enum
from typing import Final, Literal

from app.core.authz import Principal

BODY_MAX: Final = 4000
UNPUBLISH_REASON_MAX: Final = 500

NOTABLE_STATES: Final = frozenset({"confirmed", "completed"})
"""E5: notes go on confirmed sessions (before or after the start) and
completed ones."""

INTERNAL: Final = "internal"
FAMILY: Final = "family"

TUTOR_ROLE: Final = "tutor"
ACADEMIC_NOTE_REVIEW: Final = "academic_note.review"
"""The identity catalog's codes (repeated here: ADR-0002)."""

NOTE_AUTHOR_ONLY: Final = "note_author_only"
NOTE_READ_ONLY: Final = "note_read_only"
NOTE_PUBLISHED: Final = "note_published"
NOTE_NOT_PUBLISHED: Final = "note_not_published"
NOTE_ALREADY_PUBLISHED: Final = "note_already_published"
NOTE_TUTOR_ONLY: Final = "note_tutor_only"
SESSION_NOT_NOTABLE: Final = "session_not_notable"

type NoteAction = Literal["edit", "publish", "unpublish", "delete"]
type WriteBlock = Literal["note_author_only", "note_read_only"]
type StateBlock = Literal[
    "note_published", "note_not_published", "note_already_published"
]

ACTIONS: Final[tuple[NoteAction, ...]] = ("edit", "publish", "unpublish", "delete")


class NoteRelation(Enum):
    """How the caller relates to one note (E3)."""

    AUTHOR = "author"
    ASSIGNED_TUTOR = "assigned_tutor"
    REVIEWER = "reviewer"
    NONE = "none"


class SessionNoteAccess(Enum):
    """The caller of a session route (C2 step 3, E1)."""

    TUTOR = "tutor"
    REVIEWER = "reviewer"
    HIDDEN = "hidden"


def is_tutor(principal: Principal) -> bool:
    return TUTOR_ROLE in principal.roles


def is_reviewer(principal: Principal) -> bool:
    return principal.has(ACADEMIC_NOTE_REVIEW)


def note_relation(
    principal: Principal, *, author_user_id: uuid.UUID, student_assigned: bool
) -> NoteRelation:
    """E3. ``student_assigned``: the note's student is one of the caller's
    current assignments (``scheduling.service.student_ids_for_tutor``;
    ``False`` for a caller without the ``tutor`` role)."""
    tutor = is_tutor(principal)
    if tutor and principal.user_id == author_user_id:
        return NoteRelation.AUTHOR
    if tutor and student_assigned:
        return NoteRelation.ASSIGNED_TUTOR
    if is_reviewer(principal):
        return NoteRelation.REVIEWER
    return NoteRelation.NONE


def can_read(relation: NoteRelation) -> bool:
    """Every relation but ``none`` reads the note (the author always reads
    their own, also once the student is a former one)."""
    return relation is not NoteRelation.NONE


def write_block(
    action: NoteAction,
    relation: NoteRelation,
    *,
    student_assigned: bool,
    reviewer: bool,
) -> WriteBlock | None:
    """D1 step 4 for a caller who reads the note (``relation`` is not
    ``none``). Only the author edits, publishes and deletes, and only while
    the student is one of their current assignments (else
    ``note_read_only``). ``unpublish`` also lets a reviewer through
    (``reviewer``: the caller holds ``academic_note.review``), whatever the
    author's assignments."""
    author = relation is NoteRelation.AUTHOR
    if action == "unpublish" and author and student_assigned:
        return None
    if action == "unpublish" and reviewer:
        return None
    if not author:
        return NOTE_AUTHOR_ONLY
    if not student_assigned:
        return NOTE_READ_ONLY
    return None


def state_block(action: NoteAction, visibility: str) -> StateBlock | None:
    """D1 step 7: edit and delete need an ``internal`` note (``409
    note_published``), publish too (``note_already_published``); unpublish
    needs a ``family`` one (``note_not_published``)."""
    if action == "unpublish":
        return None if visibility == FAMILY else NOTE_NOT_PUBLISHED
    if visibility == INTERNAL:
        return None
    return NOTE_ALREADY_PUBLISHED if action == "publish" else NOTE_PUBLISHED


def can(
    action: NoteAction,
    relation: NoteRelation,
    *,
    student_assigned: bool,
    reviewer: bool,
    visibility: str,
) -> bool:
    """The ``can_<action>`` flag of the note view (C3)."""
    return (
        relation is not NoteRelation.NONE
        and write_block(
            action, relation, student_assigned=student_assigned, reviewer=reviewer
        )
        is None
        and state_block(action, visibility) is None
    )


def is_current_tutor(principal: Principal, tutor_user_id: uuid.UUID | None) -> bool:
    """The caller holds the ``tutor`` role and is the session's current
    tutor."""
    return (
        tutor_user_id is not None
        and is_tutor(principal)
        and principal.user_id == tutor_user_id
    )


def session_note_access(
    principal: Principal, tutor_user_id: uuid.UUID | None
) -> SessionNoteAccess:
    """C2 step 3, E1 (any state): the current tutor first, then a
    reviewer; anyone else the ``404`` of an unknown id."""
    if is_current_tutor(principal, tutor_user_id):
        return SessionNoteAccess.TUTOR
    if is_reviewer(principal):
        return SessionNoteAccess.REVIEWER
    return SessionNoteAccess.HIDDEN


def is_notable(state: str) -> bool:
    """E5: ``409 session_not_notable`` otherwise."""
    return state in NOTABLE_STATES


def unpublish_needs_reason(relation: NoteRelation) -> bool:
    """D4: anyone but the author gives a reason (and the author is
    notified)."""
    return relation is not NoteRelation.AUTHOR


def clean(value: str | None) -> str | None:
    """Trimmed; whitespace-only or missing is ``None``."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None
