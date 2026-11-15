"""Helpers for the class series tests (#53 J1-J4).

Closures are global in a module database, so each test takes its own decade
(``unique_year``) and builds its timeline in it: :class:`Timeline` puts
"now" on a Sunday at 12:00 (Mexico City, the default zone, no DST) and the
series' first Monday the next day, so the default 12-week horizon holds
exactly 12 Mondays and 12 Wednesdays.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from app.modules.identity.permissions import OWNER
from tests.integration.auth_support import create_user
from tests.integration.catalog_support import get, post, unique_year
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.test_class_session_create import MX

SERIES = "/api/v1/class-series"


@dataclass(frozen=True)
class Timeline:
    """``now`` (Sunday 12:00 local) and the Monday after it."""

    now: datetime
    monday: date

    def week(self, n: int, weekday: int = 1) -> date:
        """The local date of ISO ``weekday`` in week ``n`` (1 = the first)."""
        return self.monday + timedelta(weeks=n - 1, days=weekday - 1)

    def at(self, day: date, hhmm: str) -> datetime:
        return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=MX)


def timeline(year: int | None = None) -> Timeline:
    first = date(year or unique_year(), 1, 8)
    monday = first + timedelta(days=(8 - first.isoweekday()) % 7)
    sunday = monday - timedelta(days=1)
    return Timeline(now=datetime.combine(sunday, time(12), tzinfo=MX), monday=monday)


def series_body(
    *,
    student: uuid.UUID,
    subject: uuid.UUID,
    starts_on: date,
    tutor: uuid.UUID | None = None,
    room: uuid.UUID | None = None,
    start_time: str = "16:00",
    duration_minutes: int = 60,
    weekdays: list[int] | None = None,
    interval: int = 1,
    until: date | None = None,
    count: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "kind": "individual",
        "student_id": str(student),
        "subject_id": str(subject),
        "tutor_user_id": None if tutor is None else str(tutor),
        "classroom_id": None if room is None else str(room),
        "starts_on": starts_on.isoformat(),
        "start_time": start_time,
        "duration_minutes": duration_minutes,
        "recurrence": {
            "weekdays": [1, 3] if weekdays is None else weekdays,
            "interval": interval,
            "until": None if until is None else until.isoformat(),
            "count": count,
        },
        **extra,
    }


def create_series(client: TestClient, cookie: Any, payload: dict[str, Any]) -> Response:
    return post(client, cookie, SERIES, payload)


def read_series(client: TestClient, cookie: Any, series_id: Any) -> Response:
    return get(client, cookie, f"{SERIES}/{series_id}")


def owner_cookie(url: str, client: TestClient) -> Any:
    """A new Owner, signed in (call it under the frozen clock)."""
    owner = create_user(url, roles=(OWNER,))
    return sign_in(client, owner.username)


def counts(url: str) -> tuple[int, int, int, int]:
    """``(series, series participants, sessions, session participants)``."""
    [row] = run_sql(
        url,
        "SELECT (SELECT count(*) FROM class_series) AS a,"
        " (SELECT count(*) FROM class_series_participants) AS b,"
        " (SELECT count(*) FROM class_sessions) AS c,"
        " (SELECT count(*) FROM class_session_participants) AS d",
    )
    return (int(row.a), int(row.b), int(row.c), int(row.d))


def series_rows(url: str, series_id: Any) -> list[Any]:
    """The series' sessions by start: state, instants, codes, tutor, room."""
    return run_sql(
        url,
        "SELECT id, state, starts_at, ends_at, original_start, conflict_codes,"
        " tutor_user_id, classroom_id, scheduled_minutes, is_exception"
        " FROM class_sessions WHERE series_id = :id ORDER BY starts_at",
        {"id": str(series_id)},
    )


def iso_z(value: datetime) -> str:
    """An instant as the API writes it (UTC, ``Z``)."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
