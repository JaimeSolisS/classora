"""Session notes: internal tutor notes and parent-facing learning
information (#79 M5-12; spec §2 "Internal tutor notes are not automatically
shared with families", §4, §9, §16, W5.1, W5.3, W5.4; architecture §4.1,
§4.2).

A tutor writes notes about one participating student in a session (E1).
Every note is created ``internal``; only an explicit publish makes it
``family`` (E2), and only :func:`list_family_learning_notes` returns notes
to families: shared, live notes of family-visible sessions, as a closed
allow-list (F3). ``tests/integration/test_session_notes_leak.py`` walks
every family-reachable ``GET`` route to prove that no other response
carries a note's text.

The routes are in :mod:`app.workflows.session_notes_router`.

**Who** (E3, E4; the pure rules in ``academics.note_rules``):

- the session routes (create, the session's notes): the session's
  **current** tutor while they hold the ``tutor`` role, or an
  ``academic_note.review`` holder (a reviewer reads, ``403
  note_tutor_only`` on a create); anyone else the ``404`` of an unknown id;
- a note: its author (always readable; writable only while the student is
  one of their current assignments, ``scheduling.service
  .student_ids_for_tutor``, else ``403 note_read_only``), any tutor whose
  current assignments include the student (reads all of that student's
  notes), a reviewer (reads all; unpublishes with a reason); anyone else,
  Finance included, the ``404`` of an unknown id;
- the family route: :func:`scheduling.service.ensure_family_student_access`
  (the student, a linked guardian of a minor or of a consenting adult;
  ``403 student_consent_required`` for a linked guardian of an adult
  without the consent; anyone else ``404``, staff and tutors included).

**Create** (:func:`create_note`; the route adds ``401`` and the body's
``422``): the session unlocked → ``404`` → ``403 note_tutor_only`` → the
session ``FOR SHARE`` (the policy again on the locked row) → ``409
session_not_notable`` (not ``confirmed`` / ``completed``) → ``422
student_not_participant`` (also for an unknown student) → ``403
note_read_only`` (the student isn't a current assignment) → the
``internal`` row. Nothing is notified.

**Writes on a note** (:func:`edit_note`, :func:`publish_note`,
:func:`unpublish_note`, :func:`delete_note`; D1): ``404`` (unknown,
deleted or unreadable) → ``403`` (``note_author_only`` /
``note_read_only``) → (unpublish by a non-author: ``422 reason_required``)
→ the note ``FOR UPDATE`` → ``409 version_conflict`` → ``409`` state
(``note_published``, ``note_already_published``, ``note_not_published``)
→ the write (version + 1, the audit diff). Publishing notifies the
student's login and the guardians with academic access
(``academics.learning_note_published``); a reviewer's unpublish notifies
the author (``academics.learning_note_unpublished``). The params never
carry the text, the reason or the tutor (E7).
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, cast

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal, RecordNotFoundError
from app.core.models import ensure_version
from app.core.pagination import CursorPage
from app.modules.academics import service as academics
from app.modules.academics.schemas import (
    FamilyLearningNote,
    NotePerson,
    NoteStudent,
    NoteSubject,
    NoteVisibility,
    SessionNoteCreate,
    SessionNoteEdit,
    SessionNoteList,
    SessionNoteUnpublish,
    SessionNoteView,
)
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling import service as scheduling

rules = academics.note_rules

PUBLISHED_NOTIFICATION: Final = "academics.learning_note_published"
UNPUBLISHED_NOTIFICATION: Final = "academics.learning_note_unpublished"


class LearningNotePublishedParams(BaseModel):
    """``academics.learning_note_published`` (E7): no text, no tutor."""

    student_id: str
    class_session_id: str
    starts_at: str


class LearningNoteUnpublishedParams(BaseModel):
    """``academics.learning_note_unpublished`` (E7): no text, no reason."""

    note_id: str
    class_session_id: str
    starts_at: str


def register_note_notifications() -> None:
    """Register both types (again: a no-op); ``app.wiring`` calls it."""
    administration.register_notification_type(
        PUBLISHED_NOTIFICATION, LearningNotePublishedParams
    )
    administration.register_notification_type(
        UNPUBLISHED_NOTIFICATION, LearningNoteUnpublishedParams
    )


# --- Shared ---------------------------------------------------------------------------


async def _assigned(
    session: AsyncSession, principal: Principal
) -> frozenset[uuid.UUID]:
    """The caller's current assignments (one statement; none without the
    ``tutor`` role)."""
    if not rules.is_tutor(principal):
        return frozenset()
    return await scheduling.student_ids_for_tutor(
        session, principal.user_id, now=clock.now()
    )


@dataclass(frozen=True)
class _Caller:
    principal: Principal
    assigned: frozenset[uuid.UUID]

    def relation(self, note: academics.SessionNote) -> rules.NoteRelation:
        return rules.note_relation(
            self.principal,
            author_user_id=note.author_user_id,
            student_assigned=note.student_id in self.assigned,
        )

    def can(self, action: rules.NoteAction, note: academics.SessionNote) -> bool:
        return rules.can(
            action,
            self.relation(note),
            student_assigned=note.student_id in self.assigned,
            reviewer=rules.is_reviewer(self.principal),
            visibility=note.visibility,
        )


def _rfc3339(value: datetime) -> str:
    """An instant in UTC with ``Z`` (the notification params' format)."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def _views(
    session: AsyncSession,
    caller: _Caller,
    rows: Iterable[academics.NoteRow],
    *,
    students: Mapping[uuid.UUID, str] | None = None,
) -> list[SessionNoteView]:
    """The views of ``rows``: the students (unless given), the people and
    the subjects in at most three statements whatever the number of
    notes."""
    rows = list(rows)
    if not rows:
        return []
    if students is None:
        refs = await families.student_references(
            session, {row.note.student_id for row in rows}
        )
        students = {key: ref.display_name for key, ref in refs.items()}
    people = await identity.get_account_references(
        session,
        {row.note.author_user_id for row in rows}
        | {
            row.note.published_by_user_id
            for row in rows
            if row.note.published_by_user_id is not None
        },
    )
    subjects = await catalog.subject_references(
        session, {row.subject_id for row in rows}
    )

    def person(user_id: uuid.UUID) -> NotePerson:
        account = people.get(user_id)
        return NotePerson(display_name="" if account is None else account.display_name)

    views = []
    for row in rows:
        note = row.note
        subject = subjects.get(row.subject_id)
        views.append(
            SessionNoteView(
                id=note.id,
                version=note.version,
                class_session_id=note.class_session_id,
                session_starts_at=row.session_starts_at.astimezone(UTC),
                subject=NoteSubject(
                    id=row.subject_id, name="" if subject is None else subject.name
                ),
                student=NoteStudent(
                    id=note.student_id, display_name=students.get(note.student_id, "")
                ),
                author=person(note.author_user_id),
                is_own=note.author_user_id == caller.principal.user_id,
                body=note.body,
                visibility=cast(NoteVisibility, note.visibility),
                published_at=note.published_at,
                published_by=(
                    None
                    if note.published_by_user_id is None
                    else person(note.published_by_user_id)
                ),
                unpublished_at=note.unpublished_at,
                unpublish_reason=note.unpublish_reason,
                edited=note.body_updated_at is not None,
                created_at=note.created_at,
                can_edit=caller.can("edit", note),
                can_publish=caller.can("publish", note),
                can_unpublish=caller.can("unpublish", note),
                can_delete=caller.can("delete", note),
            )
        )
    return views


async def _view_of(
    session: AsyncSession, caller: _Caller, note: academics.SessionNote
) -> SessionNoteView:
    context = await scheduling.note_session(session, note.class_session_id)
    assert context is not None  # noqa: S101 (RESTRICT: the session stays)
    [view] = await _views(
        session,
        caller,
        [
            academics.NoteRow(
                note=note,
                session_starts_at=context.starts_at,
                subject_id=context.subject_id,
            )
        ],
    )
    return view


# --- C: create ------------------------------------------------------------------------


def _ensure_session_tutor(principal: Principal, context: object) -> None:
    """``404`` (unknown or hidden) or ``403 note_tutor_only``."""
    if context is None:
        raise RecordNotFoundError()
    assert isinstance(context, scheduling.NoteSession)  # noqa: S101
    access = rules.session_note_access(principal, context.tutor_user_id)
    if access is rules.SessionNoteAccess.HIDDEN:
        raise RecordNotFoundError()
    if access is rules.SessionNoteAccess.REVIEWER:
        raise academics.NoteTutorOnlyError()


async def create_note(
    session: AsyncSession,
    *,
    principal: Principal,
    class_session_id: uuid.UUID,
    body: SessionNoteCreate,
) -> SessionNoteView:
    """``POST /api/v1/class-sessions/{id}/notes`` (C2; the order is in the
    module docstring)."""
    _ensure_session_tutor(
        principal, await scheduling.note_session(session, class_session_id)
    )
    locked = await scheduling.note_session(session, class_session_id, share=True)
    _ensure_session_tutor(principal, locked)
    assert locked is not None  # noqa: S101 (checked above)
    if not rules.is_notable(locked.state):
        raise academics.SessionNotNotableError(locked.state)
    if not await scheduling.is_participant(session, locked.id, body.student_id):
        raise academics.NoteFieldError(
            "student_id",
            "student_not_participant",
            "The student doesn't take part in this session.",
        )
    assigned = await _assigned(session, principal)
    if body.student_id not in assigned:
        raise academics.NoteReadOnlyError()
    note = await academics.create_note(
        session,
        class_session_id=locked.id,
        student_id=body.student_id,
        author_user_id=principal.user_id,
        body=body.body,
    )
    return await _view_of(session, _Caller(principal, assigned), note)


# --- E: staff and tutor reads ---------------------------------------------------------


async def list_session_notes(
    session: AsyncSession,
    principal: Principal,
    class_session_id: uuid.UUID,
    *,
    student_id: uuid.UUID | None,
) -> SessionNoteList:
    """``GET /api/v1/class-sessions/{id}/notes`` (E1): at most 6
    statements whatever the number of notes."""
    context = await scheduling.note_session(session, class_session_id)
    if context is None:
        raise RecordNotFoundError()
    access = rules.session_note_access(principal, context.tutor_user_id)
    if access is rules.SessionNoteAccess.HIDDEN:
        raise RecordNotFoundError()
    assigned = await _assigned(session, principal)
    caller = _Caller(principal, assigned)
    rows = await academics.notes_for_session(
        session,
        context.id,
        sessions=scheduling.note_session_columns(),
        student_id=student_id,
    )
    visible = [row for row in rows if rules.can_read(caller.relation(row.note))]
    return SessionNoteList(items=await _views(session, caller, visible))


async def list_student_notes(
    session: AsyncSession,
    principal: Principal,
    student_id: uuid.UUID,
    *,
    cursor: str | None,
    limit: int,
) -> CursorPage[SessionNoteView]:
    """``GET /api/v1/students/{id}/notes`` (E2): reviewers and tutors with
    the student assigned read every note; a tutor who taught the student
    before reads only their own (read-only), and only when there is one;
    anyone else, and an unknown id, the same ``404``. At most 6
    statements."""
    reviewer = rules.is_reviewer(principal)
    if not reviewer and not rules.is_tutor(principal):
        raise RecordNotFoundError()
    refs = await families.student_references(session, [student_id])
    if student_id not in refs:
        raise RecordNotFoundError()
    assigned = await _assigned(session, principal)
    own_only = not reviewer and student_id not in assigned
    page = await academics.notes_for_student(
        session,
        student_id,
        sessions=scheduling.note_session_columns(),
        cursor=cursor,
        limit=limit,
        author_user_id=principal.user_id if own_only else None,
    )
    if own_only and not page.rows:
        raise RecordNotFoundError()
    items = await _views(
        session,
        _Caller(principal, assigned),
        page.rows,
        students={student_id: refs[student_id].display_name},
    )
    return CursorPage[SessionNoteView](items=items, next_cursor=page.next_cursor)


# --- F: the family route --------------------------------------------------------------


async def list_family_learning_notes(
    session: AsyncSession,
    principal: Principal,
    student_id: uuid.UUID,
    *,
    class_session_id: uuid.UUID | None,
    cursor: str | None,
    limit: int,
) -> CursorPage[FamilyLearningNote]:
    """``GET /api/v1/students/{id}/learning-notes`` (F1-F3): the only route
    that returns notes to families. At most 6 statements for one family
    role."""
    await scheduling.ensure_family_student_access(session, principal, student_id)
    page = await academics.family_notes_for_student(
        session,
        student_id,
        family_sessions=scheduling.note_session_columns(family_visible=True),
        class_session_id=class_session_id,
        cursor=cursor,
        limit=limit,
    )
    if not page.rows:
        return CursorPage[FamilyLearningNote](items=[], next_cursor=None)
    subjects = await catalog.subject_references(
        session, {row.subject_id for row in page.rows}
    )
    authors = await identity.get_account_references(
        session, {row.note.author_user_id for row in page.rows}
    )
    items = []
    for row in page.rows:
        note = row.note
        subject = subjects.get(row.subject_id)
        author = authors.get(note.author_user_id)
        assert note.published_at is not None  # noqa: S101 (family notes)
        items.append(
            FamilyLearningNote(
                id=note.id,
                class_session_id=note.class_session_id,
                session_starts_at=row.session_starts_at.astimezone(UTC),
                subject_name="" if subject is None else subject.name,
                author=NotePerson(
                    display_name="" if author is None else author.display_name
                ),
                body=note.body,
                published_at=note.published_at,
            )
        )
    return CursorPage[FamilyLearningNote](items=items, next_cursor=page.next_cursor)


# --- D: writes on a note --------------------------------------------------------------


_STATE_ERRORS: Final = {
    rules.NOTE_PUBLISHED: academics.NotePublishedError,
    rules.NOTE_ALREADY_PUBLISHED: academics.NoteAlreadyPublishedError,
    rules.NOTE_NOT_PUBLISHED: academics.NoteNotPublishedError,
}


async def _locked_for(
    session: AsyncSession,
    principal: Principal,
    note_id: uuid.UUID,
    action: rules.NoteAction,
    version: int,
    *,
    reason: str | None = None,
) -> tuple[_Caller, academics.SessionNote, rules.NoteRelation]:
    """D1 steps 3-7."""
    note = await academics.note_by_id(session, note_id)
    if note is None:
        raise RecordNotFoundError()
    caller = _Caller(principal, await _assigned(session, principal))
    relation = caller.relation(note)
    if not rules.can_read(relation):
        raise RecordNotFoundError()
    block = rules.write_block(
        action,
        relation,
        student_assigned=note.student_id in caller.assigned,
        reviewer=rules.is_reviewer(principal),
    )
    if block == rules.NOTE_AUTHOR_ONLY:
        raise academics.NoteAuthorOnlyError()
    if block == rules.NOTE_READ_ONLY:
        raise academics.NoteReadOnlyError()
    if (
        action == "unpublish"
        and rules.unpublish_needs_reason(relation)
        and reason is None
    ):
        raise academics.NoteFieldError("reason", "reason_required", "Give a reason.")
    locked = await academics.get_note_for_update(session, note_id)
    if locked is None:
        raise RecordNotFoundError()
    ensure_version(locked, version)
    state = rules.state_block(action, locked.visibility)
    if state is not None:
        raise _STATE_ERRORS[state]()
    return caller, locked, relation


async def edit_note(
    session: AsyncSession,
    *,
    principal: Principal,
    note_id: uuid.UUID,
    body: SessionNoteEdit,
) -> SessionNoteView:
    """``PATCH /api/v1/notes/{id}`` (D2): an identical text changes nothing."""
    caller, note, _ = await _locked_for(
        session, principal, note_id, "edit", body.version
    )
    await academics.update_note_body(session, note, body=body.body, now=clock.now())
    return await _view_of(session, caller, note)


async def publish_note(
    session: AsyncSession, *, principal: Principal, note_id: uuid.UUID, version: int
) -> SessionNoteView:
    """``POST /api/v1/notes/{id}/publish`` (D3): shared with the family,
    who are notified (again after an unpublish)."""
    caller, note, _ = await _locked_for(session, principal, note_id, "publish", version)
    await academics.publish_note(
        session, note, actor_id=principal.user_id, now=clock.now()
    )
    context = await scheduling.note_session(session, note.class_session_id)
    assert context is not None  # noqa: S101 (RESTRICT: the session stays)
    recipients = await families.academic_notification_recipients(
        session, note.student_id, today=await administration.center_today(session)
    )
    params = LearningNotePublishedParams(
        student_id=str(note.student_id),
        class_session_id=str(note.class_session_id),
        starts_at=_rfc3339(context.starts_at),
    )
    for user_id in recipients:
        await administration.create_notification(
            session,
            recipient_user_id=user_id,
            notification_type=PUBLISHED_NOTIFICATION,
            params=params,
        )
    return await _view_of(session, caller, note)


async def unpublish_note(
    session: AsyncSession,
    *,
    principal: Principal,
    note_id: uuid.UUID,
    body: SessionNoteUnpublish,
) -> SessionNoteView:
    """``POST /api/v1/notes/{id}/unpublish`` (D4): back to internal; a
    reviewer gives a reason and the author is notified."""
    caller, note, relation = await _locked_for(
        session, principal, note_id, "unpublish", body.version, reason=body.reason
    )
    await academics.unpublish_note(
        session, note, actor_id=principal.user_id, now=clock.now(), reason=body.reason
    )
    if rules.unpublish_needs_reason(relation):
        context = await scheduling.note_session(session, note.class_session_id)
        assert context is not None  # noqa: S101 (RESTRICT: the session stays)
        await administration.create_notification(
            session,
            recipient_user_id=note.author_user_id,
            notification_type=UNPUBLISHED_NOTIFICATION,
            params=LearningNoteUnpublishedParams(
                note_id=str(note.id),
                class_session_id=str(note.class_session_id),
                starts_at=_rfc3339(context.starts_at),
            ),
        )
    return await _view_of(session, caller, note)


async def delete_note(
    session: AsyncSession, *, principal: Principal, note_id: uuid.UUID, version: int
) -> None:
    """``DELETE /api/v1/notes/{id}?version=`` (D5): soft delete of an
    internal note; every route then answers ``404`` for it."""
    _, note, _ = await _locked_for(session, principal, note_id, "delete", version)
    await academics.soft_delete_note(
        session, note, actor_id=principal.user_id, now=clock.now()
    )
