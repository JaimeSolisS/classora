"""Helpers for the session and series edit tests (#54 E1-E5).

Each test takes its own decade (:func:`~tests.integration.series_support.
timeline`): "now" is the Sunday 12:00 (Mexico City) before the series'
first Monday, so the first occurrence (Monday 16:00) is past the 24-hour
booking notice and the default 12-week horizon holds every one of the 24
occurrences of a ``count = 24`` series (finite, so the nightly runs of other
tests in the module database never extend it).
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.administration import service as administration
from app.modules.catalog.models import Closure
from app.modules.families.models import Student
from app.modules.identity.permissions import GUARDIAN, OWNER, STUDENT
from app.modules.scheduling import tasks
from app.modules.scheduling.materialize import OCCURRENCE_CONFLICT_TYPE
from app.modules.scheduling.models import ClassSession
from app.modules.scheduling.sessions import transition_class_session
from tests.conftest import frozen_clock
from tests.factories.base import persist
from tests.factories.families import GuardianFactory, link_guardian
from tests.factories.identity import persist_user
from tests.integration.auth_support import CreatedUser, create_user
from tests.integration.catalog_support import get, patch, post
from tests.integration.conftest import run_sql, settings_for
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.series_support import (
    Timeline,
    create_series,
    series_body,
    timeline,
)
from tests.integration.test_assignment_offers import new_tutor_user
from tests.integration.test_class_session_create import (
    change,
    new_room,
    new_student,
    new_subject,
)

SESSIONS = "/api/v1/class-sessions"
SERIES = "/api/v1/class-series"
OFFERS = "/api/v1/assignment-offers"

OK = (200, None)
CREATED = (201, None)
NOT_FOUND = (404, "not_found")
INVALID = (422, "validation_error")
CONFLICT = (409, "scheduling_conflict")
STALE = (409, "version_conflict")

CONFLICT_REQUESTS = (
    "SELECT id, status FROM approval_requests"
    " WHERE request_type = 'scheduling.occurrence_conflict' AND subject_id = :id"
)


@dataclass
class Ctx:
    """A test's timeline, references and users."""

    url: str
    client: TestClient
    tl: Timeline
    owner: CreatedUser
    student: uuid.UUID
    subject: uuid.UUID
    tutor: CreatedUser
    room: uuid.UUID
    other_room: uuid.UUID
    cookies: dict[str, Any] = field(default_factory=dict)

    def body(self, *, tutor: bool = False, room: bool = False, **changes: Any) -> Any:
        changes.setdefault("count", 24)
        return series_body(
            student=self.student,
            subject=self.subject,
            tutor=self.tutor.id if tutor else None,
            room=self.room if room else None,
            starts_on=changes.pop("starts_on", self.tl.monday),
            **changes,
        )


def make_ctx(url: str, client: TestClient, year: int | None = None) -> Ctx:
    tl = timeline(year)
    subject = new_subject(url)
    return Ctx(
        url=url,
        client=client,
        tl=tl,
        owner=create_user(url, roles=(OWNER,)),
        student=new_student(url),
        subject=subject,
        tutor=new_tutor_user(url, qualified_for=[subject]),
        room=new_room(url),
        other_room=new_room(url),
    )


def as_user(ctx: Ctx, user: CreatedUser, at: datetime) -> Any:
    ctx.client.cookies.clear()
    with frozen_clock(at):
        return sign_in(ctx.client, user.username)


def owner_call(ctx: Ctx, at: datetime, call: Any) -> Response:
    cookie = as_user(ctx, ctx.owner, at)
    with frozen_clock(at):
        response: Response = call(cookie)
    return response


def make_series(
    ctx: Ctx, *, tutor: bool = False, accept: bool = False, **changes: Any
) -> dict[str, Any]:
    """A series created at ``tl.now``: with a tutor its rows are ``offered``
    (``accept=True``: the tutor accepts, so they are ``confirmed``)."""
    response = owner_call(
        ctx,
        ctx.tl.now,
        lambda cookie: create_series(
            ctx.client, cookie, ctx.body(tutor=tutor, **changes)
        ),
    )
    assert code_of(response) == CREATED, response.text
    created: dict[str, Any] = response.json()
    if accept:
        offer_id = created["assignment"]["offer_id"]
        cookie = as_user(ctx, ctx.tutor, ctx.tl.now)
        with frozen_clock(ctx.tl.now):
            answered = post(ctx.client, cookie, f"{OFFERS}/{offer_id}/accept", None)
        assert code_of(answered) == OK, answered.text
    return created


def patch_session(ctx: Ctx, row_id: Any, at: datetime, **body: Any) -> Response:
    payload = {
        key: (
            value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, uuid.UUID)
            else value
        )
        for key, value in body.items()
    }
    return owner_call(
        ctx,
        at,
        lambda cookie: patch(ctx.client, cookie, f"{SESSIONS}/{row_id}", payload),
    )


def edit_body(
    *,
    mode: str = "following",
    from_date: date | None = None,
    version: int = 1,
    start_time: str = "16:00",
    duration_minutes: int = 60,
    classroom_id: Any = None,
    weekdays: list[int] | None = None,
    interval: int = 1,
    until: date | None = None,
    count: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "from_date": None if from_date is None else from_date.isoformat(),
        "version": version,
        "start_time": start_time,
        "duration_minutes": duration_minutes,
        "classroom_id": None if classroom_id is None else str(classroom_id),
        "recurrence": {
            "weekdays": [1, 3] if weekdays is None else weekdays,
            "interval": interval,
            "until": None if until is None else until.isoformat(),
            "count": count,
        },
        **extra,
    }


def edit_series(ctx: Ctx, series_id: Any, at: datetime, body: Any) -> Response:
    return owner_call(
        ctx,
        at,
        lambda cookie: post(ctx.client, cookie, f"{SERIES}/{series_id}/edits", body),
    )


def cancel_series(ctx: Ctx, series_id: Any, at: datetime, body: Any) -> Response:
    return owner_call(
        ctx,
        at,
        lambda cookie: post(
            ctx.client, cookie, f"{SERIES}/{series_id}/cancellations", body
        ),
    )


def read_series_at(ctx: Ctx, series_id: Any, at: datetime) -> dict[str, Any]:
    response = owner_call(
        ctx, at, lambda cookie: get(ctx.client, cookie, f"{SERIES}/{series_id}")
    )
    assert code_of(response) == OK, response.text
    found: dict[str, Any] = response.json()
    return found


def rows_of(url: str, series_id: Any) -> list[Any]:
    """Every column of the series' rows, by original start."""
    return run_sql(
        url,
        "SELECT * FROM class_sessions WHERE series_id = :id"
        " ORDER BY original_start, id",
        {"id": str(series_id)},
    )


def row_by_id(url: str, row_id: Any) -> Any:
    [row] = run_sql(
        url, "SELECT * FROM class_sessions WHERE id = :id", {"id": str(row_id)}
    )
    return row


def series_row(url: str, series_id: Any) -> Any:
    [row] = run_sql(
        url, "SELECT * FROM class_series WHERE id = :id", {"id": str(series_id)}
    )
    return row


def on_day(rows: list[Any], ctx: Ctx, day: date) -> Any:
    """The row whose rule date is ``day``."""
    [row] = [
        r for r in rows if r.original_start.astimezone(ctx.tl.now.tzinfo).date() == day
    ]
    return row


def run_job(url: str, at: datetime) -> tasks.MaterializationResult:
    with frozen_clock(at):
        return asyncio.run(tasks.run_materialization(settings_for(url), now=at))


def notes(url: str, notification_type: str, key: str, value: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT recipient_user_id, params FROM notifications"
        " WHERE notification_type = :t AND params->>:k = :v"
        " ORDER BY recipient_user_id",
        {"t": notification_type, "k": key, "v": str(value)},
    )


def conflict_requests(url: str, row_id: Any) -> list[Any]:
    return run_sql(url, CONFLICT_REQUESTS, {"id": str(row_id)})


def offers_of(url: str, row_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT o.* FROM assignment_offers o JOIN assignment_offer_sessions x"
        " ON x.offer_id = o.id WHERE x.class_session_id = :id"
        " ORDER BY o.offered_at",
        {"id": str(row_id)},
    )


def with_family(url: str, student_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Give the student a login and a linked guardian with an account;
    returns ``(student user, guardian user)``."""

    async def _make(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
        student = await session.get(Student, student_id)
        assert student is not None
        login = await persist_user(session, roles=(STUDENT,))
        student.user_id = login.id
        account = await persist_user(session, roles=(GUARDIAN,))
        guardian = await persist(session, GuardianFactory, user_id=account.id)
        await link_guardian(session, student, guardian)
        return login.id, account.id

    return in_uow(url, _make)


def set_row(url: str, row_id: Any, **values: Any) -> None:
    async def _set(session: AsyncSession) -> None:
        await change(session, ClassSession, uuid.UUID(str(row_id)), **values)

    in_uow(url, _set)


def add_closure(url: str, starts_on: date, ends_on: date | None = None) -> uuid.UUID:
    async def _add(session: AsyncSession) -> uuid.UUID:
        closure = Closure(
            name="Día festivo",
            closure_type="holiday",
            starts_on=starts_on,
            ends_on=ends_on or starts_on,
        )
        session.add(closure)
        await session.flush()
        return closure.id

    found: uuid.UUID = in_uow(url, _add)
    return found


def lift_closure(url: str, closure_id: uuid.UUID) -> None:
    """Deactivate a closure (its dates reopen at the next reconcile)."""

    async def _lift(session: AsyncSession) -> None:
        await change(session, Closure, closure_id, is_active=False)

    in_uow(url, _lift)


def make_conflict(url: str, row_id: Any, codes: list[str]) -> None:
    """Put a stored ``unassigned`` row into ``conflict`` with an open inbox
    request, as the nightly job leaves one (``unassigned -> conflict`` is
    not a D2 move, so the state is set directly)."""

    async def _make(session: AsyncSession) -> None:
        row = await session.get(ClassSession, uuid.UUID(str(row_id)))
        assert row is not None
        row.state = "conflict"
        row.conflict_codes = codes
        await session.flush()
        await administration.open_approval_request(
            session,
            request_type=OCCURRENCE_CONFLICT_TYPE,
            subject_id=row.id,
            requester_user_id=None,
        )

    in_uow(url, _make)


def hours(n: float) -> timedelta:
    return timedelta(hours=n)


def cancel_row(url: str, row_id: Any, reason: str = "Viaje familiar") -> None:
    """Cancel one row through the shared transition (as #55 will)."""

    async def _cancel(session: AsyncSession) -> None:
        row = await session.get(ClassSession, uuid.UUID(str(row_id)))
        assert row is not None
        await transition_class_session(
            session,
            row.id,
            to_state="cancelled",
            expected_version=row.version,
            reason=reason,
        )

    in_uow(url, _cancel)


def snapshot(rows: list[Any]) -> dict[Any, dict[str, Any]]:
    """Every column of each row, by id."""
    return {row.id: dict(row._mapping) for row in rows}


def total(url: str, table: str) -> int:
    # The table names come from the tests themselves.
    [row] = run_sql(url, f"SELECT count(*) AS n FROM {table}")  # noqa: S608
    return int(row.n)
