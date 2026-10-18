"""A test-only app for the authorization framework (M2-03).

Never mounted in the real app. It has:

- sample routes, one per access kind, including the delegable permission
  ``payroll.finalize`` on a route with a path parameter and a required body
  (the check-order tests and the runtime matrix self-test use them);
- a fake in-memory note store with a sample record policy (the record-level
  tests use it);
- :data:`SAMPLE_MATRIX`, its test-local permission matrix.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, ConfigDict

from app.core.authz import (
    AUTH_RESPONSES,
    PERMISSION_RESPONSES,
    Principal,
    authenticated,
    authorize_record,
    public,
    require,
)
from app.core.conventions import install_conventions
from app.modules.identity.permissions import (
    ADMINISTRATOR,
    GUARDIAN,
    OWNER,
    PAYROLL_FINALIZE,
    ROLE_CODES,
    STUDENT_VIEW,
    TUTOR,
    USER_VIEW,
)

# --- Sample record policy (the convention of a module's permissions.py) ----------


@dataclass(frozen=True)
class Note:
    id: uuid.UUID
    tutor_id: uuid.UUID
    guardian_ids: frozenset[uuid.UUID]


def can_view_note(principal: Principal, note: Note) -> bool:
    """Its tutor sees it as a tutor, its guardians as guardians (any role)."""
    return (TUTOR in principal.roles and note.tutor_id == principal.user_id) or (
        GUARDIAN in principal.roles and principal.user_id in note.guardian_ids
    )


def can_archive_note(principal: Principal, note: Note) -> bool:
    """Only its tutor may archive it."""
    return TUTOR in principal.roles and note.tutor_id == principal.user_id


TUTOR_ID = uuid.UUID("0199a000-0000-7000-8000-00000000000a")
OTHER_TUTOR_ID = uuid.UUID("0199a000-0000-7000-8000-00000000000b")
GUARDIAN_ID = uuid.UUID("0199a000-0000-7000-8000-00000000000c")
NOTE = Note(
    id=uuid.UUID("0199a000-0000-7000-8000-0000000000f1"),
    tutor_id=TUTOR_ID,
    guardian_ids=frozenset({GUARDIAN_ID}),
)
NOTES: dict[uuid.UUID, Note] = {NOTE.id: NOTE}


class NoteOut(BaseModel):
    id: uuid.UUID


class FinalizeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str
    amount: int


class FinalizeOut(BaseModel):
    period_id: uuid.UUID
    by: uuid.UUID


def build_authz_app() -> FastAPI:
    app = FastAPI()
    install_conventions(app)
    sample = APIRouter(prefix="/sample")

    @sample.get("/public", dependencies=[public(reason="Sample public route")])
    async def sample_public() -> dict[str, str]:
        return {"status": "ok"}

    @sample.get("/me", responses=AUTH_RESPONSES)
    async def sample_me(
        principal: Annotated[Principal, authenticated()],
    ) -> dict[str, str]:
        return {"user_id": str(principal.user_id)}

    @sample.get(
        "/users", dependencies=[require(USER_VIEW)], responses=PERMISSION_RESPONSES
    )
    async def sample_users() -> list[str]:
        return []

    @sample.post(
        "/payroll/{period_id}/finalize",
        responses=PERMISSION_RESPONSES,
    )
    async def sample_finalize(
        principal: Annotated[Principal, require(PAYROLL_FINALIZE)],
        period_id: uuid.UUID,
        body: FinalizeIn,
    ) -> FinalizeOut:
        return FinalizeOut(period_id=period_id, by=principal.user_id)

    app.include_router(sample)
    app.include_router(_notes_router())
    return app


def _notes_router() -> APIRouter:
    """Record-policy routes on the note store."""
    router = APIRouter(prefix="/notes", responses=PERMISSION_RESPONSES)

    @router.get("/{note_id}")
    async def get_note(
        note_id: uuid.UUID,
        principal: Annotated[Principal, require(STUDENT_VIEW)],
    ) -> NoteOut:
        note = authorize_record(
            NOTES.get(note_id), can_view=lambda n: can_view_note(principal, n)
        )
        return NoteOut(id=note.id)

    @router.post("/{note_id}/archive")
    async def archive_note(
        note_id: uuid.UUID,
        principal: Annotated[Principal, require(STUDENT_VIEW)],
    ) -> NoteOut:
        note = authorize_record(
            NOTES.get(note_id),
            can_view=lambda n: can_view_note(principal, n),
            can_act=lambda n: can_archive_note(principal, n),
        )
        return NoteOut(id=note.id)

    return router


DOCS_REASON = "FastAPI documentation"
ALL_ROLES = list(ROLE_CODES)  # every role holds student.view (C1)

SAMPLE_MATRIX: dict[str, Any] = {
    "route": [
        {"method": "GET", "path": "/docs", "access": "public", "reason": DOCS_REASON},
        {
            "method": "GET",
            "path": "/docs/oauth2-redirect",
            "access": "public",
            "reason": DOCS_REASON,
        },
        {
            "method": "GET",
            "path": "/notes/{note_id}",
            "access": "permission",
            "permission": STUDENT_VIEW,
            "allow": ALL_ROLES,
        },
        {
            "method": "POST",
            "path": "/notes/{note_id}/archive",
            "access": "permission",
            "permission": STUDENT_VIEW,
            "allow": ALL_ROLES,
        },
        {
            "method": "GET",
            "path": "/openapi.json",
            "access": "public",
            "reason": DOCS_REASON,
        },
        {"method": "GET", "path": "/redoc", "access": "public", "reason": DOCS_REASON},
        {"method": "GET", "path": "/sample/me", "access": "authenticated"},
        {
            "method": "POST",
            "path": "/sample/payroll/{period_id}/finalize",
            "access": "permission",
            "permission": PAYROLL_FINALIZE,
            "allow": [OWNER],
            "json": {"note": "closing", "amount": 3},
        },
        {
            "method": "GET",
            "path": "/sample/public",
            "access": "public",
            "reason": "Sample public route",
        },
        {
            "method": "GET",
            "path": "/sample/users",
            "access": "permission",
            "permission": USER_VIEW,
            "allow": [OWNER, ADMINISTRATOR],
        },
    ]
}
"""The test-local matrix of :func:`build_authz_app`."""
