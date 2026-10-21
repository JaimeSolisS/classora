"""Workload warnings on the one-off session create (#50 A, D3) with real
logins through the real app.

The tutor's limits are set through #197's ``PATCH …/availability/settings``.
The data helpers and the fixed week (Monday 2031-01-06,
America/Mexico_City) come from ``test_class_session_create``.
"""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import OWNER
from app.modules.scheduling import booking
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import ClassSessionCreate
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, patch
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.test_class_session_create import (
    CREATED,
    MONDAY,
    Setup,
    at,
    body,
    create,
    existing_session,
    new_room,
    new_student,
    new_subject,
    new_tutor,
)

OK = (200, None)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture(scope="module")
def cookie(client: TestClient, owner: CreatedUser) -> Any:
    return sign_in(client, owner.username)


@pytest.fixture
def setup(module_seeded_url: str) -> Setup:
    subject = new_subject(module_seeded_url)
    return Setup(
        student=new_student(module_seeded_url),
        subject=subject,
        tutor=new_tutor(module_seeded_url, qualified_for=[subject]),
        room=new_room(module_seeded_url),
    )


def set_limits(
    client: TestClient,
    cookie: Any,
    tutor: uuid.UUID,
    *,
    daily: int | None = None,
    weekly: int | None = None,
    rest: int | None = None,
) -> None:
    response = patch(
        client,
        cookie,
        f"/api/v1/tutors/{tutor}/availability/settings",
        {
            "version": 0,
            "daily_limit_minutes": daily,
            "weekly_limit_minutes": weekly,
            "required_break_minutes": rest,
        },
    )
    assert code_of(response) == OK, response.text


def held(
    url: str, setup: Setup, day: Any, hhmm: str, minutes: int = 60, **fields: Any
) -> uuid.UUID:
    """A stored session of the setup's tutor (``confirmed`` by default) with
    another student."""
    fields.setdefault("state", "confirmed")
    return existing_session(
        url,
        starts_at=at(day, hhmm),
        minutes=minutes,
        tutor_user_id=setup.tutor,
        student_id=new_student(url),
        **fields,
    )


def created_warnings(client: TestClient, cookie: Any, payload: dict[str, Any]) -> Any:
    response = create(client, cookie, payload)
    assert code_of(response) == CREATED, response.text
    return response.json()["warnings"]


def at_ten(setup: Setup, day: Any = MONDAY, minutes: int = 60) -> dict[str, Any]:
    start = at(day, "10:00")
    return body(setup, starts_at=start, ends_at=start + timedelta(minutes=minutes))


def test_daily_limit_exceeded(module_seeded_url, client, cookie, setup):
    set_limits(client, cookie, setup.tutor, daily=90)
    held(module_seeded_url, setup, MONDAY, "08:00")

    assert created_warnings(client, cookie, at_ten(setup)) == [
        {
            "code": "daily_limit_exceeded",
            "details": {
                "date": "2031-01-06",
                "limit_minutes": 90,
                "scheduled_minutes": 120,
            },
        }
    ]


def test_weekly_limit_exceeded(module_seeded_url, client, cookie, setup):
    set_limits(client, cookie, setup.tutor, weekly=150)
    held(module_seeded_url, setup, MONDAY + timedelta(days=2), "10:00")
    held(module_seeded_url, setup, MONDAY + timedelta(days=5), "10:00")
    # The Sunday before is another ISO week.
    held(module_seeded_url, setup, MONDAY - timedelta(days=1), "10:00")

    assert created_warnings(client, cookie, at_ten(setup)) == [
        {
            "code": "weekly_limit_exceeded",
            "details": {
                "week_start": "2031-01-06",
                "limit_minutes": 150,
                "scheduled_minutes": 180,
            },
        }
    ]


def test_break_too_short(module_seeded_url, client, cookie, setup):
    set_limits(client, cookie, setup.tutor, rest=15)
    before = held(module_seeded_url, setup, MONDAY, "08:50")  # ends 09:50

    assert created_warnings(client, cookie, at_ten(setup)) == [
        {
            "code": "break_too_short",
            "details": {
                "class_session_id": str(before),
                "gap_minutes": 10,
                "required_break_minutes": 15,
            },
        }
    ]


def test_all_three_in_order(module_seeded_url, client, cookie, setup):
    set_limits(client, cookie, setup.tutor, daily=60, weekly=60, rest=30)
    before = held(module_seeded_url, setup, MONDAY, "09:00", 45)  # ends 09:45
    after = held(module_seeded_url, setup, MONDAY, "11:00")

    warnings = created_warnings(client, cookie, at_ten(setup))

    assert [(w["code"], w["details"].get("class_session_id")) for w in warnings] == [
        ("daily_limit_exceeded", None),
        ("weekly_limit_exceeded", None),
        ("break_too_short", str(before)),
        ("break_too_short", str(after)),
    ]
    assert warnings[0]["details"]["scheduled_minutes"] == 165
    assert [w["details"].get("gap_minutes") for w in warnings[2:]] == [15, 0]


def test_exactly_at_the_limits_and_equal_to_the_break(
    module_seeded_url, client, cookie, setup
):
    set_limits(client, cookie, setup.tutor, daily=120, weekly=120, rest=15)
    held(module_seeded_url, setup, MONDAY, "08:45")  # ends 09:45

    assert created_warnings(client, cookie, at_ten(setup)) == []


def test_without_a_tutor_there_are_no_warnings(
    module_seeded_url, client, cookie, setup
):
    set_limits(client, cookie, setup.tutor, daily=15)
    payload = at_ten(setup) | {"tutor_user_id": None}

    assert created_warnings(client, cookie, payload) == []


def test_without_limits_there_are_no_warnings(module_seeded_url, client, cookie, setup):
    held(module_seeded_url, setup, MONDAY, "09:00")

    assert created_warnings(client, cookie, at_ten(setup)) == []


@pytest.mark.parametrize("state", ["cancelled", "skipped_closure", "conflict"])
def test_sessions_that_do_not_hold_the_tutor_do_not_count(
    module_seeded_url, client, cookie, setup, state
):
    set_limits(client, cookie, setup.tutor, daily=60, rest=30)
    held(module_seeded_url, setup, MONDAY, "08:00", state=state)
    held(module_seeded_url, setup, MONDAY, "11:00", state=state)

    assert created_warnings(client, cookie, at_ten(setup)) == []


@pytest.mark.parametrize("state", ["offered", "completed"])
def test_offered_and_completed_sessions_count(
    module_seeded_url, client, cookie, setup, state
):
    set_limits(client, cookie, setup.tutor, daily=60)
    held(module_seeded_url, setup, MONDAY, "08:00", state=state)

    [warning] = created_warnings(client, cookie, at_ten(setup))
    assert warning["code"] == "daily_limit_exceeded"


def test_an_overridden_create_carries_its_warnings(
    module_seeded_url, client, cookie, setup
):
    # Sunday is outside the operating hours: a soft conflict, overridden.
    sunday = MONDAY + timedelta(days=6)
    set_limits(client, cookie, setup.tutor, weekly=60)
    held(module_seeded_url, setup, MONDAY, "10:00")
    payload = at_ten(setup, sunday) | {
        "override": {"codes": ["outside_operating_hours"], "reason": "Recuperación"}
    }

    warnings = created_warnings(client, cookie, payload)

    assert [w["code"] for w in warnings] == ["weekly_limit_exceeded"]


def test_the_create_takes_at_most_two_more_statements(
    module_seeded_url, client, cookie, setup, monkeypatch
):
    """A7: against the #48 create (the warnings step replaced by a no-op),
    two more statements with a tutor whose limits are set, one without
    limits, none without a tutor."""

    def run(payload: dict[str, Any], *, baseline: bool) -> int:
        model = ClassSessionCreate.model_validate(payload)

        async def none(*args: Any, **kwargs: Any) -> list[Any]:
            return []

        with monkeypatch.context() as patched:
            if baseline:
                patched.setattr(booking, "session_warnings", none)
            count, _ = count_statements(
                module_seeded_url,
                lambda session: scheduling.create_class_session(
                    session, model, actor_id=setup.tutor
                ),
            )
        return count

    def fresh(hour: int, **fields: Any) -> dict[str, Any]:
        start = at(MONDAY + timedelta(days=1), f"{hour:02d}:00")
        return body(
            setup,
            student_id=new_student(module_seeded_url),
            starts_at=start,
            ends_at=start + timedelta(minutes=30),
            **fields,
        )

    held(module_seeded_url, setup, MONDAY + timedelta(days=1), "08:00")
    untutored = run(fresh(9, tutor_user_id=None), baseline=True)
    assert run(fresh(10, tutor_user_id=None), baseline=False) == untutored
    tutored = run(fresh(11), baseline=True)
    assert run(fresh(12), baseline=False) == tutored + 1  # the limits only
    set_limits(client, cookie, setup.tutor, daily=600, weekly=6000, rest=5)
    assert run(fresh(13), baseline=False) == tutored + 2
