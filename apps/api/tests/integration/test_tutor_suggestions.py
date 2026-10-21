"""Tutor suggestions for a new slot (#51 B, C, D, F; H2, H3, H5) with real
logins through the real app.

The tests share one seeded database per module; each test makes its own
subject, so only the tutors qualified for it are candidates (and closures,
which are global, use their own dates). Slots are in the fixed week of
Monday 2031-01-06 in the default center zone (America/Mexico_City, UTC-6, no
DST); the helpers come from the #48 create tests.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import Role, UserRole
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    FINANCE_STAFF,
    OWNER,
    TUTOR,
)
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import TutorSuggestionQuery
from app.modules.staff.models import (
    TutorAvailabilitySettings,
    TutorPreferredClassroom,
    TutorQualification,
)
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, unique_year
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.test_class_session_create import (
    MONDAY,
    MONDAY_10,
    MONDAY_11,
    at,
    close_center,
    existing_session,
    new_room,
    new_subject,
    new_tutor,
    revoke_all,
    time_off,
)

SUGGESTIONS = "/api/v1/tutor-suggestions"
INVALID = (422, "validation_error")
DECIDED = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def query(
    subject: uuid.UUID,
    *,
    starts_at: datetime | str = MONDAY_10,
    ends_at: datetime | str = MONDAY_11,
    **fields: Any,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "subject_id": str(subject),
        "starts_at": starts_at if isinstance(starts_at, str) else starts_at.isoformat(),
        "ends_at": ends_at if isinstance(ends_at, str) else ends_at.isoformat(),
    }
    for key, value in fields.items():
        params[key] = str(value) if isinstance(value, uuid.UUID) else value
    return params


def suggest(client: TestClient, cookie: Any, params: dict[str, Any]) -> Response:
    return get(client, cookie, SUGGESTIONS, **params)


def listed(response: Response) -> list[uuid.UUID]:
    assert response.status_code == 200, response.text
    return [uuid.UUID(item["tutor"]["id"]) for item in response.json()["items"]]


def field_errors(response: Response) -> list[tuple[str, str]]:
    assert code_of(response) == INVALID, response.text
    return [(e["path"][-1], e["code"]) for e in response.json()["errors"]]


# --- Data ----------------------------------------------------------------------------


def qualification(
    url: str, user_id: uuid.UUID, subject: uuid.UUID, status: str
) -> None:
    """A qualification of ``user_id`` for ``subject`` in ``status``."""

    async def _add(session: AsyncSession) -> None:
        fields: dict[str, Any] = {}
        if status != "pending":
            fields |= {"decided_at": DECIDED, "decided_by_user_id": user_id}
        if status == "rejected":
            fields["rejection_reason"] = "Sin certificado"
        if status == "revoked":
            fields |= {
                "revoked_at": DECIDED,
                "revoked_by_user_id": user_id,
                "revocation_reason": "Ya no imparte la materia",
            }
        session.add(
            TutorQualification(
                tutor_user_id=user_id, subject_id=subject, status=status, **fields
            )
        )

    in_uow(url, _add)


def remove_tutor_role(url: str, user_id: uuid.UUID) -> None:
    """Take the ``tutor`` role away (through the ORM: audited table)."""

    async def _remove(session: AsyncSession) -> None:
        role_id = await session.scalar(select(Role.id).where(Role.code == TUTOR))
        row = await session.scalar(
            select(UserRole).where(
                UserRole.user_id == user_id, UserRole.role_id == role_id
            )
        )
        assert row is not None
        await session.delete(row)
        await session.flush()

    in_uow(url, _remove)


def set_limits(
    url: str, tutor: uuid.UUID, *, daily: int | None = None, weekly: int | None = None
) -> None:
    async def _add(session: AsyncSession) -> None:
        session.add(
            TutorAvailabilitySettings(
                user_id=tutor,
                daily_limit_minutes=daily,
                weekly_limit_minutes=weekly,
            )
        )

    in_uow(url, _add)


def prefer_room(url: str, tutor: uuid.UUID, room: uuid.UUID) -> None:
    async def _add(session: AsyncSession) -> None:
        session.add(TutorPreferredClassroom(tutor_user_id=tutor, classroom_id=room))

    in_uow(url, _add)


def teaching(
    url: str, tutor: uuid.UUID, starts_at: datetime, minutes: int, state: str
) -> uuid.UUID:
    """A stored session with ``tutor`` (``conflict`` sessions keep one)."""
    return existing_session(
        url, starts_at=starts_at, minutes=minutes, state=state, tutor_user_id=tutor
    )


def row_counts(url: str) -> tuple[int, int, int]:
    [row] = run_sql(
        url,
        "SELECT (SELECT count(*) FROM class_sessions) AS s,"
        " (SELECT count(*) FROM audit_events) AS a,"
        " (SELECT count(*) FROM notifications) AS n",
    )
    return (int(row.s), int(row.a), int(row.n))


# --- Fixtures ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def coordinator(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))


@pytest.fixture(scope="module")
def cookie(client: TestClient, coordinator: CreatedUser) -> Any:
    # Coordinators do tutor assignments (spec §2): schedule.assign, no
    # schedule.manage.
    return sign_in(client, coordinator.username)


@pytest.fixture
def subject(module_seeded_url: str) -> uuid.UUID:
    return new_subject(module_seeded_url)


@pytest.fixture
def control(module_seeded_url: str, subject: uuid.UUID) -> uuid.UUID:
    """An otherwise-eligible tutor that is always listed."""
    return new_tutor(module_seeded_url, qualified_for=[subject])


# --- B1: candidates ------------------------------------------------------------------


@pytest.mark.parametrize("status", ["pending", "rejected", "cancelled", "revoked"])
def test_only_approved_qualifications_count(
    module_seeded_url, client, cookie, subject, control, status
):
    tutor = new_tutor(module_seeded_url)
    qualification(module_seeded_url, tutor, subject, status)

    assert listed(suggest(client, cookie, query(subject))) == [control]


def test_no_qualification_or_another_subject_of_the_level(
    module_seeded_url, client, cookie, subject, control
):
    new_tutor(module_seeded_url)
    new_tutor(module_seeded_url, qualified_for=[new_subject(module_seeded_url)])

    assert listed(suggest(client, cookie, query(subject))) == [control]


def test_a_revoked_qualification_is_dropped_live(
    module_seeded_url, client, cookie, subject, control
):
    tutor = new_tutor(module_seeded_url, qualified_for=[subject])
    assert set(listed(suggest(client, cookie, query(subject)))) == {control, tutor}

    revoke_all(module_seeded_url, tutor)

    assert listed(suggest(client, cookie, query(subject))) == [control]


@pytest.mark.parametrize(
    "overrides",
    [{"state": "suspended"}, {"state": "pending", "password_hash": None}],
    ids=["suspended", "pending"],
)
def test_inactive_accounts_are_dropped(
    module_seeded_url, client, cookie, subject, control, overrides
):
    new_tutor(module_seeded_url, qualified_for=[subject], **overrides)

    assert listed(suggest(client, cookie, query(subject))) == [control]


def test_a_user_without_the_tutor_role_is_dropped(
    module_seeded_url, client, cookie, subject, control
):
    # Otherwise eligible (qualified, available all week), then the tutor role
    # is removed: only the role check can drop them.
    former = new_tutor(module_seeded_url, qualified_for=[subject])
    assert set(listed(suggest(client, cookie, query(subject)))) == {control, former}

    remove_tutor_role(module_seeded_url, former)

    assert listed(suggest(client, cookie, query(subject))) == [control]


# --- B2: availability ----------------------------------------------------------------


def test_outside_the_weekly_windows_or_without_windows(
    module_seeded_url, client, cookie, subject, control
):
    new_tutor(
        module_seeded_url, qualified_for=[subject], windows=[(1, time(16), time(20))]
    )
    new_tutor(module_seeded_url, qualified_for=[subject], windows=[])

    assert listed(suggest(client, cookie, query(subject))) == [control]


def test_time_off_over_the_slot(module_seeded_url, client, cookie, subject, control):
    tutor = new_tutor(module_seeded_url, qualified_for=[subject])
    time_off(module_seeded_url, tutor, MONDAY)

    assert listed(suggest(client, cookie, query(subject))) == [control]


def test_a_center_closure_drops_nobody(module_seeded_url, client, cookie, subject):
    day = date(unique_year(), 1, 6)
    close_center(module_seeded_url, day)
    tutor = new_tutor(module_seeded_url, qualified_for=[subject])

    response = suggest(
        client,
        cookie,
        query(subject, starts_at=at(day, "10:00"), ends_at=at(day, "11:00")),
    )

    assert listed(response) == [tutor]


# --- B3: overlap ---------------------------------------------------------------------


@pytest.mark.parametrize("state", ["offered", "confirmed", "completed"])
def test_an_overlapping_session_as_tutor_drops(
    module_seeded_url, client, cookie, subject, control, state
):
    tutor = new_tutor(module_seeded_url, qualified_for=[subject])
    teaching(module_seeded_url, tutor, at(MONDAY, "10:30"), 60, state)

    assert listed(suggest(client, cookie, query(subject))) == [control]


@pytest.mark.parametrize("state", ["cancelled", "skipped_closure", "conflict"])
def test_non_occupying_sessions_never_block(
    module_seeded_url, client, cookie, subject, state
):
    tutor = new_tutor(module_seeded_url, qualified_for=[subject])
    teaching(module_seeded_url, tutor, at(MONDAY, "10:00"), 60, state)
    # An unassigned session has no tutor; any one at the same time blocks no one.
    existing_session(module_seeded_url, starts_at=MONDAY_10, state="unassigned")

    assert listed(suggest(client, cookie, query(subject))) == [tutor]


def test_back_to_back_is_allowed(module_seeded_url, client, cookie, subject):
    tutor = new_tutor(module_seeded_url, qualified_for=[subject])
    teaching(module_seeded_url, tutor, at(MONDAY, "09:00"), 60, "confirmed")
    teaching(module_seeded_url, tutor, at(MONDAY, "11:00"), 60, "offered")

    assert listed(suggest(client, cookie, query(subject))) == [tutor]


# --- D2: 422s ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("starts_at", "ends_at", "expected"),
    [
        (
            "2031-01-06T10:00:30-06:00",
            "2031-01-06T11:00:00-06:00",
            [("starts_at", "not_whole_minutes")],
        ),
        (
            "2031-01-06T10:00:00-06:00",
            "2031-01-06T09:00:00-06:00",
            [("ends_at", "ends_before_start")],
        ),
        (
            "2031-01-06T10:00:00-06:00",
            "2031-01-06T10:10:00-06:00",
            [("ends_at", "duration_out_of_range")],
        ),
        (
            "2031-01-06T22:00:00-06:00",
            "2031-01-07T01:00:00-06:00",
            [("ends_at", "crosses_local_date")],
        ),
        (
            "2031-01-06T10:00:00",
            "2031-01-06T11:00:00-06:00",
            [("starts_at", "naive_datetime")],
        ),
    ],
    ids=["whole-minutes", "ends-before", "duration", "crosses", "naive"],
)
def test_bounds(client, cookie, subject, starts_at, ends_at, expected):
    response = suggest(
        client, cookie, query(subject, starts_at=starts_at, ends_at=ends_at)
    )

    assert field_errors(response) == expected


def test_a_plus_offset_sent_percent_encoded(module_seeded_url, client, cookie, subject):
    tutor = new_tutor(module_seeded_url, qualified_for=[subject])

    response = suggest(
        client,
        cookie,
        query(
            subject,
            starts_at="2031-01-06T17:00:00+01:00",
            ends_at="2031-01-06T18:00:00+01:00",
        ),
    )

    assert listed(response) == [tutor]


def test_references_all_at_once(module_seeded_url, client, cookie):
    inactive = new_subject(module_seeded_url, is_active=False)
    room = new_room(module_seeded_url, is_active=False)

    response = suggest(client, cookie, query(inactive, classroom_id=room))

    assert field_errors(response) == [
        ("subject_id", "subject_inactive"),
        ("classroom_id", "classroom_inactive"),
    ]
    unknown = suggest(client, cookie, query(uuid.uuid4(), classroom_id=uuid.uuid4()))
    assert field_errors(unknown) == [
        ("subject_id", "subject_unknown"),
        ("classroom_id", "classroom_unknown"),
    ]


def test_an_inactive_level_is_subject_inactive(module_seeded_url, client, cookie):
    subject = new_subject(module_seeded_url, level_active=False)

    assert field_errors(suggest(client, cookie, query(subject))) == [
        ("subject_id", "subject_inactive")
    ]


def test_bounds_come_before_references(client, cookie):
    response = suggest(
        client,
        cookie,
        query(uuid.uuid4(), ends_at="2031-01-06T09:00:00-06:00"),
    )

    assert field_errors(response) == [("ends_at", "ends_before_start")]


@pytest.mark.parametrize("limit", [0, 51])
def test_limit_out_of_range(client, cookie, subject, limit):
    assert code_of(suggest(client, cookie, query(subject, limit=limit))) == INVALID


def test_missing_or_malformed_parameters(client, cookie, subject):
    assert code_of(get(client, cookie, SUGGESTIONS)) == INVALID
    bad = query(subject) | {"subject_id": "nope"}
    assert code_of(suggest(client, cookie, bad)) == INVALID


def test_nobody_qualifies_is_an_empty_list(client, cookie, subject):
    response = suggest(client, cookie, query(subject))

    assert response.status_code == 200
    assert response.json() == {"items": [], "eligible_count": 0}


# --- C, H3: ranking ------------------------------------------------------------------


def three_tutors(url: str, subject: uuid.UUID) -> list[uuid.UUID]:
    return [
        new_tutor(url, qualified_for=[subject], display_name=name)
        for name in ("Ana Uno", "Beto Dos", "Carla Tres")
    ]


def test_weekly_minutes_ascending(module_seeded_url, client, cookie, subject):
    ana, beto, carla = three_tutors(module_seeded_url, subject)
    teaching(
        module_seeded_url,
        ana,
        at(MONDAY + timedelta(days=2), "10:00"),
        120,
        "confirmed",
    )
    teaching(
        module_seeded_url, beto, at(MONDAY + timedelta(days=3), "10:00"), 60, "offered"
    )

    response = suggest(client, cookie, query(subject))

    assert listed(response) == [carla, beto, ana]
    items = response.json()["items"]
    assert [i["rank"] for i in items] == [1, 2, 3]
    assert [(i["weekly_minutes"], i["daily_minutes"]) for i in items] == [
        (0, 0),
        (60, 0),
        (120, 0),
    ]
    assert response.json()["eligible_count"] == 3


def test_equal_weekly_daily_decides(module_seeded_url, client, cookie, subject):
    ana, beto, _ = three_tutors(module_seeded_url, subject)
    # Both 90 minutes this week; Ana's are on the slot's day.
    teaching(module_seeded_url, ana, at(MONDAY, "14:00"), 90, "confirmed")
    teaching(
        module_seeded_url,
        beto,
        at(MONDAY + timedelta(days=1), "14:00"),
        90,
        "confirmed",
    )

    items = suggest(client, cookie, query(subject)).json()["items"]

    names = [
        (i["tutor"]["display_name"], i["weekly_minutes"], i["daily_minutes"])
        for i in items
    ]
    assert names == [("Carla Tres", 0, 0), ("Beto Dos", 90, 0), ("Ana Uno", 90, 90)]


def test_equal_minutes_preferred_room_first(module_seeded_url, client, cookie, subject):
    ana, beto, carla = three_tutors(module_seeded_url, subject)
    room = new_room(module_seeded_url)
    prefer_room(module_seeded_url, carla, room)

    with_room = suggest(client, cookie, query(subject, classroom_id=room))
    without = suggest(client, cookie, query(subject))

    assert listed(with_room) == [carla, ana, beto]
    assert [i["prefers_classroom"] for i in with_room.json()["items"]] == [
        True,
        False,
        False,
    ]
    assert listed(without) == [ana, beto, carla]
    assert not any(i["prefers_classroom"] for i in without.json()["items"])


def test_previous_week_and_cancelled_sessions_dont_count(
    module_seeded_url, client, cookie, subject
):
    tutor = new_tutor(module_seeded_url, qualified_for=[subject])
    # Sunday 23:00 local before the slot's week is 05:00 UTC on Monday.
    teaching(
        module_seeded_url,
        tutor,
        at(MONDAY - timedelta(days=1), "23:00"),
        60,
        "confirmed",
    )
    teaching(
        module_seeded_url,
        tutor,
        at(MONDAY + timedelta(days=1), "10:00"),
        60,
        "cancelled",
    )
    # Sunday 23:00 local at the end of the week counts.
    teaching(
        module_seeded_url,
        tutor,
        at(MONDAY + timedelta(days=6), "23:00"),
        60,
        "completed",
    )

    [item] = suggest(client, cookie, query(subject)).json()["items"]

    assert (item["weekly_minutes"], item["daily_minutes"]) == (60, 0)


def test_limit_and_eligible_count(module_seeded_url, client, cookie, subject):
    three_tutors(module_seeded_url, subject)

    body = suggest(client, cookie, query(subject, limit=2)).json()

    assert len(body["items"]) == 2
    assert body["eligible_count"] == 3


def test_limits_are_returned_and_never_reorder(
    module_seeded_url, client, cookie, subject
):
    ana, beto, _ = three_tutors(module_seeded_url, subject)
    set_limits(module_seeded_url, ana, weekly=60, daily=None)
    set_limits(module_seeded_url, beto, daily=30, weekly=None)
    # Ana is already over her weekly limit and still ranks by minutes.
    teaching(
        module_seeded_url, ana, at(MONDAY + timedelta(days=2), "10:00"), 90, "confirmed"
    )
    teaching(
        module_seeded_url,
        beto,
        at(MONDAY + timedelta(days=2), "10:00"),
        120,
        "confirmed",
    )

    items = suggest(client, cookie, query(subject)).json()["items"]

    assert [
        (
            i["tutor"]["display_name"],
            i["weekly_limit_minutes"],
            i["daily_limit_minutes"],
        )
        for i in items
    ] == [("Carla Tres", None, None), ("Ana Uno", 60, None), ("Beto Dos", None, 30)]


# --- Access --------------------------------------------------------------------------


@pytest.mark.parametrize("role", [OWNER, FINANCE_STAFF, TUTOR])
def test_who_may_ask(module_seeded_url, client, subject, role):
    user = create_user(module_seeded_url, roles=(role,))
    response = suggest(client, sign_in(client, user.username), query(subject))

    expected = 200 if role == OWNER else 403
    assert response.status_code == expected, response.text


# --- F2, H5: statement budget and no writes -----------------------------------------


@pytest.mark.parametrize("count", [1, 4])
def test_statement_budget(module_seeded_url, subject, count):
    room = new_room(module_seeded_url)
    for _ in range(count):
        tutor = new_tutor(module_seeded_url, qualified_for=[subject])
        prefer_room(module_seeded_url, tutor, room)
    # Tutors without the qualification cost nothing.
    for _ in range(3):
        new_tutor(module_seeded_url)

    async def call(session: AsyncSession) -> Any:
        return await scheduling.suggest_for_slot(
            session,
            TutorSuggestionQuery(
                subject_id=subject,
                starts_at=MONDAY_10,
                ends_at=MONDAY_11,
                classroom_id=room,
            ),
        )

    statements, result = count_statements(module_seeded_url, call)

    assert result.eligible_count == count
    assert statements <= 8 + 6 * count


def test_nothing_is_written(module_seeded_url, client, cookie, subject):
    tutor = new_tutor(module_seeded_url, qualified_for=[subject])
    before = row_counts(module_seeded_url)

    assert listed(suggest(client, cookie, query(subject))) == [tutor]
    assert row_counts(module_seeded_url) == before
