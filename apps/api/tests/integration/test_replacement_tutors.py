"""Replacement tutors for an existing session (#51 E, F; H4, H5) with real
logins through the real app. Same data conventions as
``test_tutor_suggestions.py`` (one subject per test, the week of Monday
2031-01-06 in America/Mexico_City).
"""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.permissions import ACADEMIC_COORDINATOR, FINANCE_STAFF
from app.modules.scheduling import service as scheduling
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.test_class_session_create import (
    MONDAY,
    MONDAY_10,
    at,
    existing_session,
    new_room,
    new_subject,
    new_tutor,
)
from tests.integration.test_tutor_suggestions import prefer_room, row_counts, teaching

SESSIONS = "/api/v1/class-sessions"


def path(session_id: uuid.UUID) -> str:
    return f"{SESSIONS}/{session_id}/replacement-tutors"


def replacements(
    client: TestClient, cookie: Any, session_id: uuid.UUID, **params: Any
) -> Response:
    return get(client, cookie, path(session_id), **params)


def listed(response: Response) -> list[uuid.UUID]:
    assert response.status_code == 200, response.text
    return [uuid.UUID(item["tutor"]["id"]) for item in response.json()["items"]]


def session_for(
    url: str,
    subject: uuid.UUID,
    *,
    state: str = "confirmed",
    tutor: uuid.UUID | None = None,
    **fields: Any,
) -> uuid.UUID:
    if tutor is not None:
        fields["tutor_user_id"] = tutor
    return existing_session(
        url, starts_at=MONDAY_10, state=state, subject_id=subject, **fields
    )


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def coordinator(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(ACADEMIC_COORDINATOR,))


@pytest.fixture(scope="module")
def cookie(client: TestClient, coordinator: CreatedUser) -> Any:
    return sign_in(client, coordinator.username)


@pytest.fixture
def subject(module_seeded_url: str) -> uuid.UUID:
    return new_subject(module_seeded_url)


# --- E2: the current tutor is excluded -----------------------------------------------


def test_the_current_tutor_is_never_listed(module_seeded_url, client, cookie, subject):
    current = new_tutor(module_seeded_url, qualified_for=[subject])
    other = new_tutor(module_seeded_url, qualified_for=[subject])
    session_id = session_for(module_seeded_url, subject, tutor=current)

    assert listed(replacements(client, cookie, session_id)) == [other]


def test_an_unassigned_session_excludes_nobody(
    module_seeded_url, client, cookie, subject
):
    tutors = {new_tutor(module_seeded_url, qualified_for=[subject]) for _ in range(2)}
    session_id = session_for(module_seeded_url, subject, state="unassigned")

    assert set(listed(replacements(client, cookie, session_id))) == tutors


def test_a_conflict_session_with_a_tutor(module_seeded_url, client, cookie, subject):
    current = new_tutor(module_seeded_url, qualified_for=[subject])
    other = new_tutor(module_seeded_url, qualified_for=[subject])
    session_id = session_for(
        module_seeded_url, subject, state="conflict", tutor=current
    )

    assert listed(replacements(client, cookie, session_id)) == [other]


# --- E1: the session's own slot ------------------------------------------------------


def test_the_session_counts_for_nobody(module_seeded_url, client, cookie, subject):
    # An offered session's tutor is its current one; the row itself neither
    # overlaps nor adds minutes for anyone (it is excluded by id).
    current = new_tutor(module_seeded_url, qualified_for=[subject])
    other = new_tutor(module_seeded_url, qualified_for=[subject])
    session_id = session_for(module_seeded_url, subject, state="offered", tutor=current)

    [item] = replacements(client, cookie, session_id).json()["items"]

    assert uuid.UUID(item["tutor"]["id"]) == other
    assert (item["weekly_minutes"], item["daily_minutes"]) == (0, 0)


def test_the_slot_is_the_sessions(module_seeded_url, client, cookie, subject):
    room = new_room(module_seeded_url)
    busy = new_tutor(module_seeded_url, qualified_for=[subject], display_name="A")
    free = new_tutor(module_seeded_url, qualified_for=[subject], display_name="B")
    prefers = new_tutor(module_seeded_url, qualified_for=[subject], display_name="C")
    prefer_room(module_seeded_url, prefers, room)
    # Another subject's tutor is never a candidate.
    new_tutor(module_seeded_url, qualified_for=[new_subject(module_seeded_url)])
    teaching(module_seeded_url, busy, at(MONDAY, "10:30"), 60, "confirmed")
    session_id = session_for(
        module_seeded_url, subject, state="unassigned", classroom_id=room
    )

    response = replacements(client, cookie, session_id)

    assert listed(response) == [prefers, free]
    assert response.json()["items"][0]["prefers_classroom"] is True


def test_other_sessions_still_count(module_seeded_url, client, cookie, subject):
    current = new_tutor(module_seeded_url, qualified_for=[subject])
    other = new_tutor(module_seeded_url, qualified_for=[subject])
    teaching(
        module_seeded_url,
        other,
        at(MONDAY + timedelta(days=1), "10:00"),
        45,
        "confirmed",
    )
    session_id = session_for(module_seeded_url, subject, tutor=current)

    [item] = replacements(client, cookie, session_id).json()["items"]

    assert (item["weekly_minutes"], item["daily_minutes"]) == (45, 0)


def test_an_inactive_subject_still_gets_suggestions(module_seeded_url, client, cookie):
    subject = new_subject(module_seeded_url, is_active=False)
    tutor = new_tutor(module_seeded_url, qualified_for=[subject])
    session_id = session_for(module_seeded_url, subject, state="unassigned")

    assert listed(replacements(client, cookie, session_id)) == [tutor]


# --- E3: errors ----------------------------------------------------------------------


def test_unknown_session(client, cookie):
    assert code_of(replacements(client, cookie, uuid.uuid4())) == (404, "not_found")


@pytest.mark.parametrize("state", ["completed", "cancelled", "skipped_closure"])
def test_not_assignable(module_seeded_url, client, cookie, subject, state):
    session_id = session_for(module_seeded_url, subject, state=state)

    response = replacements(client, cookie, session_id)

    assert code_of(response) == (409, "class_session_not_assignable")
    assert response.json()["detail"] == "This session can no longer take a tutor."


@pytest.mark.parametrize("limit", [0, 51])
def test_limit_out_of_range_comes_before_404(client, cookie, limit):
    response = replacements(client, cookie, uuid.uuid4(), limit=limit)

    assert code_of(response) == (422, "validation_error")


def test_limit(module_seeded_url, client, cookie, subject):
    for _ in range(3):
        new_tutor(module_seeded_url, qualified_for=[subject])
    session_id = session_for(module_seeded_url, subject, state="unassigned")

    body = replacements(client, cookie, session_id, limit=2).json()

    assert (len(body["items"]), body["eligible_count"]) == (2, 3)


def test_finance_is_denied(module_seeded_url, client, subject):
    finance = create_user(module_seeded_url, roles=(FINANCE_STAFF,))
    session_id = session_for(module_seeded_url, subject, state="unassigned")

    response = replacements(client, sign_in(client, finance.username), session_id)

    assert code_of(response) == (403, "permission_denied")


# --- F2, H5 --------------------------------------------------------------------------


@pytest.mark.parametrize("count", [1, 4])
def test_statement_budget(module_seeded_url, subject, count):
    room = new_room(module_seeded_url)
    for _ in range(count):
        tutor = new_tutor(module_seeded_url, qualified_for=[subject])
        prefer_room(module_seeded_url, tutor, room)
    session_id = session_for(
        module_seeded_url, subject, state="unassigned", classroom_id=room
    )

    async def call(session: AsyncSession) -> Any:
        return await scheduling.suggest_replacements(session, session_id, limit=10)

    statements, result = count_statements(module_seeded_url, call)

    assert result.eligible_count == count
    assert statements <= 8 + 6 * count


def test_nothing_is_written(module_seeded_url, client, cookie, subject):
    current = new_tutor(module_seeded_url, qualified_for=[subject])
    other = new_tutor(module_seeded_url, qualified_for=[subject])
    session_id = session_for(module_seeded_url, subject, tutor=current)
    before = row_counts(module_seeded_url)

    assert listed(replacements(client, cookie, session_id)) == [other]
    assert row_counts(module_seeded_url) == before
