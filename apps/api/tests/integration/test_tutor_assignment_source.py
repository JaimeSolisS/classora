"""The tutor assignment source (#67 D1-D3, G3): the students of the
sessions a tutor currently teaches, wired into the ``families`` student
record policy (``app/wiring.py``), with real logins through the real app.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.families import service as families
from app.modules.families.assignments import get_tutor_assignment_source
from app.modules.identity.permissions import GUARDIAN, STUDENT, TUTOR
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.models import ClassSession
from app.wiring import register_approval_wiring
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.schedule_support import principal_of, stored
from tests.integration.test_class_session_create import change, new_student

NOW = datetime(2031, 6, 15, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module", autouse=True)
def frozen() -> Any:
    with frozen_clock(NOW):
        yield


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def ended_ago(days: int, hour: int = 0) -> datetime:
    """The start of a 60-minute session that ended ``days`` days before
    :data:`NOW` (plus ``hour`` hours earlier, to keep a tutor's sessions
    apart)."""
    return NOW - timedelta(days=days, hours=1 + hour)


def test_the_source_follows_the_d1_rule(module_seeded_url):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR,))
    other = create_user(url, roles=(TUTOR,))
    s = {name: new_student(url) for name in "abcdefghi"}
    t = tutor.id
    stored(url, NOW + timedelta(days=3), tutor_user_id=t, students=(s["a"],))
    stored(
        url,
        NOW + timedelta(days=4),
        state="offered",
        tutor_user_id=t,
        students=(s["b"],),
    )
    stored(url, ended_ago(29), state="completed", tutor_user_id=t, students=(s["c"],))
    stored(url, ended_ago(31), state="completed", tutor_user_id=t, students=(s["d"],))
    # The boundary is on ends_at (groomed mutation 7): a 3-hour session that
    # started more than 30 days ago but ended less than 30 days ago counts
    # (the mirror, one that ended 31 days ago, is "d").
    boundary = NOW - timedelta(days=30)
    stored(
        url,
        boundary - timedelta(hours=1),
        minutes=180,
        state="completed",
        tutor_user_id=t,
        students=(s["i"],),
    )
    stored(url, NOW + timedelta(days=5), state="unassigned", students=(s["e"],))
    stored(
        url,
        NOW + timedelta(days=6),
        state="cancelled",
        seen=True,
        tutor_user_id=t,
        students=(s["f"],),
    )
    stored(url, NOW + timedelta(days=7), tutor_user_id=other.id, students=(s["g"],))
    stored(
        url,
        NOW + timedelta(days=8),
        state="skipped_closure",
        seen=True,
        tutor_user_id=t,
        students=(s["h"],),
    )

    async def _read(session: AsyncSession) -> frozenset[uuid.UUID]:
        return await scheduling.student_ids_for_tutor(session, t, now=NOW)

    count, found = count_statements(url, _read)

    assert found == {s["a"], s["b"], s["c"], s["i"]}
    assert count == 1
    assert scheduling.TUTOR_ASSIGNMENT_LOOKBACK_DAYS == 30


def test_the_app_wires_the_scheduling_source():
    register_approval_wiring()

    assert get_tutor_assignment_source() is scheduling.TUTOR_ASSIGNMENT_SOURCE


def test_a_tutor_sees_their_students_at_the_limited_level(module_seeded_url, client):
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR,))
    taught, former, stranger = new_student(url), new_student(url), new_student(url)
    stored(url, NOW + timedelta(days=2), tutor_user_id=tutor.id, students=(taught,))
    stored(
        url,
        ended_ago(31),
        state="completed",
        tutor_user_id=tutor.id,
        students=(former,),
    )
    cookie = sign_in(client, tutor.username)

    listed = get(client, cookie, "/api/v1/students", status="all", limit=100).json()
    detail = get(client, cookie, f"/api/v1/students/{taught}")

    assert [item["id"] for item in listed["items"]] == [str(taught)]
    assert code_of(detail) == (200, None)
    # The limited level: no personal data (#33 E2).
    assert detail.json()["personal"] is None
    for hidden in (former, stranger):
        assert code_of(get(client, cookie, f"/api/v1/students/{hidden}")) == (
            404,
            "not_found",
        )


def test_a_substitution_ends_the_link_at_once(module_seeded_url, client):
    """The tutor changes on the row (as a #55 accept does): the original
    tutor no longer sees the student on their next request."""
    url = module_seeded_url
    tutor = create_user(url, roles=(TUTOR,))
    substitute = create_user(url, roles=(TUTOR,))
    student = new_student(url)
    session_id = stored(
        url, NOW + timedelta(days=9), tutor_user_id=tutor.id, students=(student,)
    )
    cookie = sign_in(client, tutor.username)
    assert code_of(get(client, cookie, f"/api/v1/students/{student}")) == (200, None)

    async def _hand_over(session: AsyncSession) -> None:
        await change(
            session, ClassSession, uuid.UUID(session_id), tutor_user_id=substitute.id
        )

    in_uow(url, _hand_over)

    assert code_of(get(client, cookie, f"/api/v1/students/{student}")) == (
        404,
        "not_found",
    )


@pytest.mark.parametrize("roles", [(GUARDIAN,), (STUDENT,), (GUARDIAN, STUDENT)])
def test_facts_load_assignments_only_for_the_tutor_role(module_seeded_url, roles):
    url = module_seeded_url
    user = create_user(url, roles=roles)
    stored(url, NOW + timedelta(days=10), tutor_user_id=user.id, students=())
    register_approval_wiring()
    source = get_tutor_assignment_source()

    async def _load(session: AsyncSession) -> Any:
        return await families.load_access_facts(
            session, principal_of(user, *roles), source
        )

    count, facts = count_statements(url, _load)
    tutor_count, tutor_facts = count_statements(
        url,
        lambda session: families.load_access_facts(
            session, principal_of(user, *roles, TUTOR), source
        ),
    )

    assert facts.assigned_student_ids == frozenset()
    # One statement per family role; the tutor role adds exactly one.
    assert count == len(roles)
    assert tutor_count == len(roles) + 1
    assert tutor_facts.assigned_student_ids == frozenset()
