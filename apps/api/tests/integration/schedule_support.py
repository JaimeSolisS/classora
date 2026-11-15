"""Helpers for the role-scoped schedule tests (#67) with real logins through
the real app.

Each test module reads its own weeks (the first Monday of a
``unique_year()``), so other modules' sessions never show up. The center
zone is the default America/Mexico_City.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.identity.permissions import ROLE_PERMISSIONS
from app.modules.scheduling.models import ClassSession
from tests.factories.scheduling import add_participant, persist_class_session
from tests.integration.auth_support import CreatedUser
from tests.integration.catalog_support import get
from tests.integration.lifecycle_support import in_uow

FAMILY_STATES = {"confirmed", "completed", "cancelled", "skipped_closure"}
TUTOR_STATES = {"offered", *FAMILY_STATES}
"""The states each schedule can show (``first_confirmed_at`` decides for
``cancelled`` and ``skipped_closure``)."""

ALL_STATES = (
    "unassigned",
    "offered",
    "confirmed",
    "completed",
    "cancelled",
    "skipped_closure",
    "conflict",
)


def z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def tutor_path(user_id: Any) -> str:
    return f"/api/v1/tutors/{user_id}/schedule"


def student_path(student_id: Any) -> str:
    return f"/api/v1/students/{student_id}/schedule"


FAMILY_STUDENTS = "/api/v1/schedule/family-students"


def week(day: date) -> tuple[datetime, datetime]:
    """``[day 00:00 UTC, +7 days)``."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    return start, start + timedelta(days=7)


def read(
    client: TestClient,
    cookie: Any,
    path: str,
    start: datetime,
    end: datetime,
    **params: Any,
) -> Response:
    return get(client, cookie, path, **{"from": z(start), "to": z(end), **params})


def ids_of(response: Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def principal_of(user: CreatedUser | uuid.UUID, *roles: str) -> Principal:
    """The principal of ``user`` with ``roles`` and their permissions."""
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS[role]
    user_id = user if isinstance(user, uuid.UUID) else user.id
    return Principal(
        user_id=user_id, roles=frozenset(roles), permissions=frozenset(permissions)
    )


def stored(
    url: str,
    starts_at: datetime,
    *,
    state: str = "confirmed",
    students: tuple[uuid.UUID, ...] = (),
    seen: bool | None = None,
    minutes: int = 60,
    **overrides: Any,
) -> str:
    """A stored session with these participants. ``seen=True`` sets
    ``first_confirmed_at`` (a session the family saw confirmed), ``False``
    leaves it ``NULL``; ``None`` keeps the factory's default (set for
    ``confirmed``/``completed`` only)."""
    if seen is not None:
        overrides["first_confirmed_at"] = (
            starts_at - timedelta(days=3) if seen else None
        )

    async def _make(session: AsyncSession) -> str:
        made: ClassSession = await persist_class_session(
            session,
            state=state,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=minutes),
            scheduled_minutes=minutes,
            **overrides,
        )
        for student_id in students:
            await add_participant(session, made, student_id)
        return str(made.id)

    return in_uow(url, _make)
