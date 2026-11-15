"""Session note routes (#79; :mod:`app.workflows.session_notes` gives the
record policies and the orders).

The routes live here, next to the workflow they call, because a module's
router may not import ``app.workflows`` (ADR-0002). Every route is
``authenticated()`` plus a record policy (#21); the ``404`` of a hidden
record is the body of an unknown id.

- ``POST /api/v1/class-sessions/{id}/notes``: the session's current tutor
  (``403 note_tutor_only`` for an ``academic_note.review`` holder).
- ``GET /api/v1/class-sessions/{id}/notes[?student_id]``: the current tutor
  or a reviewer.
- ``GET /api/v1/students/{id}/notes[?cursor&limit]``: reviewers, tutors with
  the student assigned, and a former tutor for their own notes.
- ``PATCH /api/v1/notes/{id}``, ``POST …/publish``, ``POST …/unpublish``,
  ``DELETE /api/v1/notes/{id}?version=``: the author (unpublish also a
  reviewer, with a reason).
- ``GET /api/v1/students/{id}/learning-notes[?class_session_id&cursor&limit]``:
  the family route (shared notes only): the student, or a linked guardian
  with academic access.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response

from app.core.authz import PERMISSION_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.pagination import CursorPage
from app.core.uow import UnitOfWorkDep
from app.modules.academics.schemas import (
    FamilyLearningNote,
    SessionNoteCreate,
    SessionNoteEdit,
    SessionNoteList,
    SessionNoteUnpublish,
    SessionNoteVersion,
    SessionNoteView,
)
from app.workflows import session_notes as workflow

session_router = APIRouter(prefix="/api/v1/class-sessions", tags=["notes"])
student_router = APIRouter(prefix="/api/v1/students", tags=["notes"])
note_router = APIRouter(prefix="/api/v1/notes", tags=["notes"])

PAGE_LIMIT: Final = 20
PAGE_MAX: Final = 50

type PageLimit = Annotated[int, Query(ge=1, le=PAGE_MAX)]

_SESSION_NOT_FOUND: Final = problem_response_doc(
    "No such class session, or not one whose notes you may see (not_found)."
)
_NOTE_NOT_FOUND: Final = problem_response_doc(
    "No such note, it was deleted, or it isn't one you may see (not_found)."
)
_STUDENT_NOT_FOUND: Final = problem_response_doc(
    "No such student, or not one whose notes you may see (not_found)."
)
_WRITE_403: Final = problem_response_doc(
    "Only the note's author can do this (note_author_only); the student is no"
    " longer one of your current assignments (note_read_only); a failed Origin"
    " or X-CSRF-Token check (csrf_failed); privacy_notice_required."
)
_VERSION_422: Final = problem_response_doc(
    "The body is not valid (validation_error): no version, or an unknown field."
)


@session_router.post(
    "/{class_session_id}/notes",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "Only the session's tutor writes notes (note_tutor_only); the student"
            " is no longer one of your current assignments (note_read_only); a"
            " failed Origin or X-CSRF-Token check (csrf_failed);"
            " privacy_notice_required."
        ),
        404: _SESSION_NOT_FOUND,
        409: problem_response_doc(
            "Notes go on confirmed or completed sessions only"
            " (session_not_notable, details.state). Nothing is changed."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): an empty note"
            " (body_required), one over 4000 characters (too_long), or a student"
            " who doesn't take part in the session (student_not_participant at"
            " student_id; also for an unknown student)."
        ),
    },
)
async def create_session_note(
    principal: Annotated[Principal, authenticated()],
    class_session_id: uuid.UUID,
    body: SessionNoteCreate,
    uow: UnitOfWorkDep,
) -> SessionNoteView:
    """Write a note about one student of your session. It is internal:
    families never see it unless you share it. Nobody is notified."""
    return await workflow.create_note(
        uow.session,
        principal=principal,
        class_session_id=class_session_id,
        body=body,
    )


@session_router.get(
    "/{class_session_id}/notes",
    responses={**PERMISSION_RESPONSES, 404: _SESSION_NOT_FOUND},
)
async def list_session_notes(
    principal: Annotated[Principal, authenticated()],
    class_session_id: uuid.UUID,
    uow: UnitOfWorkDep,
    student_id: uuid.UUID | None = None,
) -> SessionNoteList:
    """The notes on a session (every author), oldest first, optionally for one
    student. For the session's tutor and staff with academic_note.review."""
    return await workflow.list_session_notes(
        uow.session, principal, class_session_id, student_id=student_id
    )


@student_router.get(
    "/{student_id}/notes",
    responses={**PERMISSION_RESPONSES, 404: _STUDENT_NOT_FOUND},
)
async def list_student_notes(
    principal: Annotated[Principal, authenticated()],
    student_id: uuid.UUID,
    uow: UnitOfWorkDep,
    cursor: str | None = None,
    limit: PageLimit = PAGE_LIMIT,
) -> CursorPage[SessionNoteView]:
    """A student's notes across sessions, newest first. Staff with
    academic_note.review and tutors currently assigned to the student read
    them all; a tutor who taught the student before reads their own,
    read-only."""
    return await workflow.list_student_notes(
        uow.session, principal, student_id, cursor=cursor, limit=limit
    )


@student_router.get(
    "/{student_id}/learning-notes",
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "The adult student hasn't given you access to their academic"
            " records (student_consent_required); privacy_notice_required."
        ),
        404: problem_response_doc(
            "No such student, or not you or one of your children (not_found)."
        ),
    },
)
async def list_family_learning_notes(
    principal: Annotated[Principal, authenticated()],
    student_id: uuid.UUID,
    uow: UnitOfWorkDep,
    class_session_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: PageLimit = PAGE_LIMIT,
) -> CursorPage[FamilyLearningNote]:
    """The notes tutors shared with the family, newest first, optionally for
    one class. For the student and their guardians with access to their
    academic records."""
    return await workflow.list_family_learning_notes(
        uow.session,
        principal,
        student_id,
        class_session_id=class_session_id,
        cursor=cursor,
        limit=limit,
    )


@note_router.patch(
    "/{note_id}",
    responses={
        **PERMISSION_RESPONSES,
        403: _WRITE_403,
        404: _NOTE_NOT_FOUND,
        409: problem_response_doc(
            "The note changed since it was read (version_conflict) or is shared"
            " with the family (note_published: stop sharing it first)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): no version, an empty note"
            " (body_required) or one over 4000 characters (too_long)."
        ),
    },
)
async def edit_note(
    principal: Annotated[Principal, authenticated()],
    note_id: uuid.UUID,
    body: SessionNoteEdit,
    uow: UnitOfWorkDep,
) -> SessionNoteView:
    """Change the text of your internal note. The same text changes
    nothing."""
    return await workflow.edit_note(
        uow.session, principal=principal, note_id=note_id, body=body
    )


@note_router.post(
    "/{note_id}/publish",
    responses={
        **PERMISSION_RESPONSES,
        403: _WRITE_403,
        404: _NOTE_NOT_FOUND,
        409: problem_response_doc(
            "The note changed since it was read (version_conflict) or is already"
            " shared (note_already_published)."
        ),
        422: _VERSION_422,
    },
)
async def publish_note(
    principal: Annotated[Principal, authenticated()],
    note_id: uuid.UUID,
    body: SessionNoteVersion,
    uow: UnitOfWorkDep,
) -> SessionNoteView:
    """Share your note with the family: the student and their guardians with
    access to their academic records see it and are notified."""
    return await workflow.publish_note(
        uow.session, principal=principal, note_id=note_id, version=body.version
    )


@note_router.post(
    "/{note_id}/unpublish",
    responses={
        **PERMISSION_RESPONSES,
        403: _WRITE_403,
        404: _NOTE_NOT_FOUND,
        409: problem_response_doc(
            "The note changed since it was read (version_conflict) or isn't"
            " shared (note_not_published)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): no version, no reason"
            " from someone who isn't the author (reason_required), or one over"
            " 500 characters (reason_too_long)."
        ),
    },
)
async def unpublish_note(
    principal: Annotated[Principal, authenticated()],
    note_id: uuid.UUID,
    body: SessionNoteUnpublish,
    uow: UnitOfWorkDep,
) -> SessionNoteView:
    """Stop sharing a note: it is internal again. Staff with
    academic_note.review give a reason, and the author is notified."""
    return await workflow.unpublish_note(
        uow.session, principal=principal, note_id=note_id, body=body
    )


@note_router.delete(
    "/{note_id}",
    status_code=204,
    response_class=Response,
    responses={
        **PERMISSION_RESPONSES,
        403: _WRITE_403,
        404: _NOTE_NOT_FOUND,
        409: problem_response_doc(
            "The note changed since it was read (version_conflict) or is shared"
            " with the family (note_published: stop sharing it first)."
        ),
        422: problem_response_doc(
            "The version query parameter is missing or not an integer"
            " (validation_error)."
        ),
    },
)
async def delete_note(
    principal: Annotated[Principal, authenticated()],
    note_id: uuid.UUID,
    version: Annotated[int, Query(description="The note's version, as last read.")],
    uow: UnitOfWorkDep,
) -> None:
    """Delete your internal note. It disappears from every list."""
    await workflow.delete_note(
        uow.session, principal=principal, note_id=note_id, version=version
    )
