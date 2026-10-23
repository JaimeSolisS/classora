"""Helpers for the change request tests (#62) with real logins through the
real app.

Each test takes its own decade (``series_edit_support.make_ctx``): "now"
(``tl.now``) is the Sunday 12:00 (Mexico City) before the first Monday and
every call runs under a frozen clock. The scene's session is a ``confirmed``
one-off individual session on the second Tuesday at 10:00 (about 214 hours
ahead: an on-time change), for a **minor** student with two linked
guardians, taught by the ctx tutor (available all day) in the ctx room.
"""

import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.families.models import Student
from app.modules.identity.permissions import GUARDIAN, STUDENT
from tests.conftest import frozen_clock
from tests.factories.base import persist
from tests.factories.families import (
    GuardianFactory,
    link_guardian,
    login_consent,
    persist_student,
    student_login,
)
from tests.integration.auth_support import CreatedUser, create_user
from tests.integration.catalog_support import get, post
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.series_edit_support import Ctx, as_user, make_ctx
from tests.integration.test_session_cancellation import one_off

CHANGE_REQUESTS = "/api/v1/change-requests"
OK = (200, None)
CREATED = (201, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
STALE = (409, "version_conflict")
PENDING = (409, "change_request_pending")
NOT_PENDING = (409, "change_request_not_pending")
STARTED = (409, "change_request_session_started")
CHANGED = (409, "change_request_session_changed")
CONFLICT = (409, "scheduling_conflict")
SLOT_UNAVAILABLE = (409, "slot_unavailable")
CONSENT = (403, "student_consent_required")
ROUTE_REQUIRED = (409, "change_request_review_route_required")

APPROVED_TYPE = "scheduling.change_request_approved"
REJECTED_TYPE = "scheduling.change_request_rejected"
CANCELLED_TYPE = "scheduling.session_cancelled"
RESCHEDULED_TYPE = "scheduling.session_rescheduled"


@dataclass
class Scene:
    """A ctx with a minor student, two linked guardians and a confirmed
    session."""

    ctx: Ctx
    guardian: CreatedUser
    other_guardian: CreatedUser
    session: dict[str, Any]

    @property
    def url(self) -> str:
        return self.ctx.url

    @property
    def start(self) -> datetime:
        return datetime.fromisoformat(self.session["starts_at"])

    def at(self, day: date, hhmm: str) -> datetime:
        return self.ctx.tl.at(day, hhmm)


def minor_student(url: str, year: int) -> uuid.UUID:
    async def _make(session: AsyncSession) -> uuid.UUID:
        student = await persist_student(
            session, contacts=0, birthdate=date(year - 10, 5, 1)
        )
        return student.id

    return in_uow(url, _make)


def adult_student(url: str, year: int) -> uuid.UUID:
    async def _make(session: AsyncSession) -> uuid.UUID:
        student = await persist_student(
            session, contacts=0, birthdate=date(year - 30, 5, 1)
        )
        return student.id

    return in_uow(url, _make)


def add_guardian(
    url: str, student_id: uuid.UUID, *, consent: bool = False, **user: Any
) -> CreatedUser:
    """A ``guardian`` account with an active profile linked to the student
    (with ``consent``: the ``guardian_academic_access`` consent)."""
    account = create_user(url, roles=(GUARDIAN,), **user)

    async def _make(session: AsyncSession) -> None:
        student = await session.get(Student, student_id)
        assert student is not None
        guardian = await persist(session, GuardianFactory, user_id=account.id)
        await link_guardian(session, student, guardian)
        if consent:
            await login_consent(
                session,
                student,
                guardian,
                recorded_by=account.id,
                scope="guardian_academic_access",
            )

    in_uow(url, _make)
    return account


def add_student_login(url: str, student_id: uuid.UUID) -> CreatedUser:
    """A ``student`` account that is the student's own login."""
    account = create_user(url, roles=(STUDENT,))

    async def _make(session: AsyncSession) -> None:
        student = await session.get(Student, student_id)
        assert student is not None
        await student_login(session, student, account.id)

    in_uow(url, _make)
    return account


def make_scene(url: str, client: TestClient, *, confirmed: bool = True) -> Scene:
    ctx = make_ctx(url, client)
    ctx = replace(ctx, student=minor_student(url, ctx.tl.monday.year))
    guardian = add_guardian(url, ctx.student)
    other = add_guardian(url, ctx.student)
    session = one_off(ctx, accept=confirmed)
    return Scene(ctx=ctx, guardian=guardian, other_guardian=other, session=session)


def call(scene: Scene, user: CreatedUser, at: datetime | None, fn: Any) -> Response:
    """Sign ``user`` in at ``at`` (default now) and run ``fn(cookie)`` under
    the frozen clock."""
    when = at or scene.ctx.tl.now
    cookie = as_user(scene.ctx, user, when)
    with frozen_clock(when):
        response: Response = fn(cookie)
    return response


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    return value


def create(
    scene: Scene,
    user: CreatedUser,
    *,
    kind: str = "cancellation",
    session_id: Any = None,
    at: datetime | None = None,
    **body: Any,
) -> Response:
    payload = _json(
        {
            "class_session_id": session_id or scene.session["id"],
            "kind": kind,
            **body,
        }
    )
    return call(
        scene,
        user,
        at,
        lambda cookie: post(scene.ctx.client, cookie, CHANGE_REQUESTS, payload),
    )


def created(response: Response) -> dict[str, Any]:
    assert code_of(response) == CREATED, response.text
    found: dict[str, Any] = response.json()
    return found


def ok(response: Response) -> dict[str, Any]:
    assert code_of(response) == OK, response.text
    found: dict[str, Any] = response.json()
    return found


def path(request_id: Any, action: str | None = None) -> str:
    return f"{CHANGE_REQUESTS}/{request_id}" + (f"/{action}" if action else "")


def approve(
    scene: Scene,
    request_id: Any,
    *,
    version: int = 1,
    at: datetime | None = None,
    user: CreatedUser | None = None,
    **body: Any,
) -> Response:
    payload = _json({"version": version, **body})
    return call(
        scene,
        user or scene.ctx.owner,
        at,
        lambda cookie: post(
            scene.ctx.client, cookie, path(request_id, "approve"), payload
        ),
    )


def reject(
    scene: Scene,
    request_id: Any,
    reason: Any = "Sin disponibilidad",
    *,
    version: int = 1,
    at: datetime | None = None,
    user: CreatedUser | None = None,
) -> Response:
    return call(
        scene,
        user or scene.ctx.owner,
        at,
        lambda cookie: post(
            scene.ctx.client,
            cookie,
            path(request_id, "reject"),
            {"version": version, "reason": reason},
        ),
    )


def review(
    scene: Scene,
    request_id: Any,
    *,
    at: datetime | None = None,
    user: CreatedUser | None = None,
) -> Response:
    return call(
        scene,
        user or scene.ctx.owner,
        at,
        lambda cookie: get(scene.ctx.client, cookie, path(request_id, "review")),
    )


def withdraw(
    scene: Scene,
    user: CreatedUser,
    request_id: Any,
    *,
    version: int = 1,
    at: datetime | None = None,
) -> Response:
    return call(
        scene,
        user,
        at,
        lambda cookie: post(
            scene.ctx.client, cookie, path(request_id, "withdraw"), {"version": version}
        ),
    )


def read(
    scene: Scene, user: CreatedUser, request_id: Any, *, at: datetime | None = None
) -> Response:
    return call(
        scene,
        user,
        at,
        lambda cookie: get(scene.ctx.client, cookie, path(request_id)),
    )


def listing(
    scene: Scene, user: CreatedUser, *, at: datetime | None = None, **params: Any
) -> Response:
    return call(
        scene,
        user,
        at,
        lambda cookie: get(
            scene.ctx.client,
            cookie,
            CHANGE_REQUESTS,
            **{key: str(value) for key, value in params.items()},
        ),
    )


def request_row(url: str, request_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT * FROM session_change_requests WHERE id = :id",
        {"id": str(request_id)},
    )
    return row


def approval_of(url: str, request_id: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT id, status, request_type, requester_user_id, priority, reason"
        " FROM approval_requests WHERE subject_id = :s",
        {"s": str(request_id)},
    )
    return row


def notes_to(url: str, user_id: Any, notification_type: str) -> list[Any]:
    return run_sql(
        url,
        "SELECT recipient_user_id, params FROM notifications"
        " WHERE recipient_user_id = :u AND notification_type = :t"
        " ORDER BY created_at, id",
        {"u": str(user_id), "t": notification_type},
    )


def notes_of_type(url: str, notification_type: str, key: str, value: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT recipient_user_id, params FROM notifications"
        " WHERE notification_type = :t AND params ->> :k = :v"
        " ORDER BY recipient_user_id",
        {"t": notification_type, "k": key, "v": str(value)},
    )


def hours(n: float) -> timedelta:
    return timedelta(hours=n)
