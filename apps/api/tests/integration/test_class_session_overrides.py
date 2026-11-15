"""Overriding soft conflicts of a one-off session create (#49 G1-G6) with real
logins through the real app.

The data helpers and the fixed week (Monday 2031-01-06, America/Mexico_City)
come from ``test_class_session_create``. Sunday 2031-01-12 is outside the
operating hours (closed), so a session then has exactly one soft conflict,
``outside_operating_hours``, unless a test adds more.
"""

import uuid
from collections.abc import Callable
from datetime import date, time, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import service as identity
from app.modules.identity.permissions import ADMINISTRATOR, OWNER
from app.modules.scheduling import service as scheduling
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, unique_year
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.test_class_session_create import (
    CREATED,
    INVALID,
    MONDAY,
    SESSIONS,
    SUNDAY,
    Setup,
    at,
    body,
    close_center,
    codes_of,
    create,
    existing_session,
    new_room,
    new_student,
    new_subject,
    new_tutor,
)

FORBIDDEN = (403, "permission_denied")
EVENT = "scheduling.session_override"
OWNER_NAME = "Dueña de Prueba"
REASON = "La familia lo pidió; el tutor está de acuerdo."


def sunday(setup: Setup, **fields: Any) -> dict[str, Any]:
    return body(
        setup, starts_at=at(SUNDAY, "10:00"), ends_at=at(SUNDAY, "11:00"), **fields
    )


def with_override(
    payload: dict[str, Any], *codes: str, reason: Any = REASON
) -> dict[str, Any]:
    return payload | {"override": {"codes": list(codes), "reason": reason}}


def counts(url: str) -> tuple[int, int, int, int]:
    [row] = run_sql(
        url,
        "SELECT (SELECT count(*) FROM class_sessions) AS s,"
        " (SELECT count(*) FROM class_session_participants) AS p,"
        " (SELECT count(*) FROM class_session_overrides) AS o,"
        " (SELECT count(*) FROM audit_events) AS a",
    )
    return (int(row.s), int(row.p), int(row.o), int(row.a))


def override_rows(url: str, session_id: Any) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, codes, conflicts, reason, actor_user_id"
        " FROM class_session_overrides WHERE class_session_id = :id",
        {"id": str(session_id)},
    )


def override_events(url: str, session_id: Any) -> list[Any]:
    return events(url, record_id=uuid.UUID(str(session_id)), action=EVENT)


def field_errors(response: Response) -> list[tuple[list[Any], str]]:
    assert code_of(response) == INVALID, response.text
    return [(e["path"], e["code"]) for e in response.json()["errors"]]


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,), display_name=OWNER_NAME)


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


# --- G1: allowed overrides -----------------------------------------------------------


def test_the_owner_overrides_two_soft_conflicts(
    module_seeded_url, client, cookie, owner, setup
):
    unqualified = new_tutor(module_seeded_url)
    payload = sunday(setup, tutor_user_id=unqualified)
    assert codes_of(create(client, cookie, payload)) == [
        ("outside_operating_hours", "soft"),
        ("tutor_not_qualified", "soft"),
    ]

    response = create(
        client,
        cookie,
        with_override(
            payload,
            "tutor_not_qualified",
            "outside_operating_hours",
            reason=f"  {REASON}  ",
        ),
    )

    assert code_of(response) == CREATED, response.text
    created = response.json()
    [row] = override_rows(module_seeded_url, created["id"])
    assert row.codes == ["outside_operating_hours", "tutor_not_qualified"]
    assert row.conflicts == [
        {
            "code": "outside_operating_hours",
            "details": {
                "date": SUNDAY.isoformat(),
                "opens_at": None,
                "closes_at": None,
            },
        },
        {"code": "tutor_not_qualified", "details": {"subject_id": str(setup.subject)}},
    ]
    assert row.reason == REASON
    assert row.actor_user_id == owner.id
    [event] = override_events(module_seeded_url, created["id"])
    assert event.record_type == "class_sessions"
    assert event.actor_user_id == owner.id
    assert event.details == {
        "override_id": str(row.id),
        "codes": row.codes,
        "conflicts": row.conflicts,
        "reason": REASON,
    }
    [create_event] = events(
        module_seeded_url, record_id=uuid.UUID(created["id"]), action="create"
    )
    assert event.request_id and event.request_id == create_event.request_id
    [item] = created["overrides"]
    assert item["id"] == str(row.id)
    assert item["codes"] == row.codes
    assert item["reason"] == REASON
    assert item["actor"] == {"id": str(owner.id), "display_name": OWNER_NAME}
    # The GET shows the same overrides.
    detail = get(client, cookie, f"{SESSIONS}/{created['id']}").json()
    assert detail == {k: v for k, v in created.items() if k != "warnings"}


def _tutor_unavailable(url: str, setup: Setup) -> dict[str, Any]:
    tutor = new_tutor(
        url, qualified_for=[setup.subject], windows=[(1, time(16), time(20))]
    )
    return body(setup, tutor_user_id=tutor)


def _room_unavailable(url: str, setup: Setup) -> dict[str, Any]:
    room = new_room(url, windows=[(1, time(16), time(20))])
    return body(setup, classroom_id=room)


def _center_closed(url: str, setup: Setup) -> dict[str, Any]:
    day = date(unique_year(), 3, 1)
    while day.isoweekday() != 2:  # a Tuesday: open 09:00-20:00
        day += timedelta(days=1)
    close_center(url, day)
    return body(setup, starts_at=at(day, "10:00"), ends_at=at(day, "11:00"))


def _outside_operating_hours(url: str, setup: Setup) -> dict[str, Any]:
    return sunday(setup)


def _tutor_not_qualified(url: str, setup: Setup) -> dict[str, Any]:
    return body(setup, tutor_user_id=new_tutor(url))


SOFT_CASES: dict[str, Callable[[str, Setup], dict[str, Any]]] = {
    "tutor_unavailable": _tutor_unavailable,
    "room_unavailable": _room_unavailable,
    "center_closed": _center_closed,
    "outside_operating_hours": _outside_operating_hours,
    "tutor_not_qualified": _tutor_not_qualified,
}


@pytest.mark.parametrize("code", sorted(SOFT_CASES))
def test_each_soft_code_can_be_overridden(
    module_seeded_url, client, cookie, setup, code
):
    payload = SOFT_CASES[code](module_seeded_url, setup)
    assert codes_of(create(client, cookie, payload)) == [(code, "soft")]

    response = create(client, cookie, with_override(payload, code))

    assert code_of(response) == CREATED, response.text
    assert [o["codes"] for o in response.json()["overrides"]] == [[code]]
    [row] = override_rows(module_seeded_url, response.json()["id"])
    assert row.codes == [code]


def test_the_notice_window_can_be_overridden(module_seeded_url, client, setup):
    owner = create_user(module_seeded_url, roles=(OWNER,))
    start = at(date(2031, 2, 4), "09:00")
    payload = body(setup, starts_at=start, ends_at=start + timedelta(hours=1))

    with frozen_clock(at(date(2031, 2, 3), "10:00")):
        cookie = sign_in(client, owner.username)
        refused = create(client, cookie, payload)
        response = create(client, cookie, with_override(payload, "notice_window"))

    assert codes_of(refused) == [("notice_window", "soft")]
    assert code_of(response) == CREATED, response.text
    [row] = override_rows(module_seeded_url, response.json()["id"])
    assert row.codes == ["notice_window"]
    assert row.conflicts == [
        {"code": "notice_window", "details": {"booking_notice_hours": 24}}
    ]


def _delegate(url: str, owner: CreatedUser, admin: CreatedUser, grant: bool) -> None:
    async def _change(session: AsyncSession) -> None:
        operation = identity.grant_delegation if grant else identity.revoke_delegation
        await operation(
            session,
            actor_id=owner.id,
            user_id=admin.id,
            permission="schedule.override",
        )

    in_uow(url, _change, actor_id=owner.id)


def test_a_delegated_administrator_overrides_until_revoked(
    module_seeded_url, client, owner, setup
):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    admin_cookie = sign_in(client, admin.username)
    first = with_override(sunday(setup), "outside_operating_hours")
    second = with_override(
        sunday(setup, student_id=new_student(module_seeded_url), tutor_user_id=None),
        "outside_operating_hours",
    )
    assert code_of(create(client, admin_cookie, first)) == FORBIDDEN

    _delegate(module_seeded_url, owner, admin, grant=True)
    allowed = create(client, admin_cookie, first)
    _delegate(module_seeded_url, owner, admin, grant=False)
    revoked = create(client, admin_cookie, second)

    assert code_of(allowed) == CREATED, allowed.text
    [row] = override_rows(module_seeded_url, allowed.json()["id"])
    assert row.actor_user_id == admin.id
    assert code_of(revoked) == FORBIDDEN


def test_listed_codes_that_do_not_occur_are_ignored(
    module_seeded_url, client, cookie, setup
):
    payload = with_override(
        sunday(setup), "center_closed", "outside_operating_hours", "notice_window"
    )

    response = create(client, cookie, payload)

    assert code_of(response) == CREATED, response.text
    session_id = response.json()["id"]
    [row] = override_rows(module_seeded_url, session_id)
    assert row.codes == ["outside_operating_hours"]
    assert [c["code"] for c in row.conflicts] == ["outside_operating_hours"]
    [event] = override_events(module_seeded_url, session_id)
    assert event.details["codes"] == ["outside_operating_hours"]
    assert response.json()["overrides"][0]["codes"] == ["outside_operating_hours"]


def test_an_override_without_any_conflict_stores_nothing(
    module_seeded_url, client, cookie, setup
):
    response = create(client, cookie, with_override(body(setup), "center_closed"))

    assert code_of(response) == CREATED, response.text
    session_id = response.json()["id"]
    assert response.json()["overrides"] == []
    assert override_rows(module_seeded_url, session_id) == []
    assert override_events(module_seeded_url, session_id) == []


def test_the_read_with_overrides_takes_at_most_seven_statements(
    module_seeded_url, client, cookie, setup
):
    created = create(
        client, cookie, with_override(sunday(setup), "outside_operating_hours")
    ).json()

    count, detail = count_statements(
        module_seeded_url,
        lambda session: scheduling.get_class_session(session, uuid.UUID(created["id"])),
    )

    assert count <= 7
    assert detail.model_dump(mode="json") == {
        k: v for k, v in created.items() if k != "warnings"
    }
    assert len(detail.overrides) == 1


# --- G2: hard conflicts always refuse --------------------------------------------


@pytest.mark.parametrize("hard", ["student_overlap", "tutor_overlap", "room_overlap"])
def test_a_hard_conflict_refuses_whatever_the_override_says(
    module_seeded_url, client, cookie, setup, hard
):
    holder = {
        "student_overlap": {"student_id": setup.student},
        "tutor_overlap": {
            "tutor_user_id": setup.tutor,
            "student_id": new_student(module_seeded_url),
            "state": "offered",
        },
        "room_overlap": {"classroom_id": setup.room},
    }[hard]
    existing_session(module_seeded_url, starts_at=at(SUNDAY, "10:30"), **holder)
    before = counts(module_seeded_url)

    response = create(
        client, cookie, with_override(sunday(setup), "outside_operating_hours")
    )

    assert codes_of(response) == [(hard, "hard"), ("outside_operating_hours", "soft")]
    assert counts(module_seeded_url) == before


@pytest.mark.parametrize(
    "code",
    ["student_overlap", "tutor_overlap", "room_overlap", "room_capacity_exceeded"],
)
def test_a_hard_code_in_the_list_is_not_overridable(
    module_seeded_url, client, cookie, setup, code
):
    before = counts(module_seeded_url)

    response = create(
        client, cookie, with_override(sunday(setup), "outside_operating_hours", code)
    )

    assert field_errors(response) == [
        (["body", "override", "codes", 1], "code_not_overridable")
    ]
    assert counts(module_seeded_url) == before


# --- G3: the permission ------------------------------------------------------------


@pytest.mark.parametrize("conflicting", [True, False])
def test_an_administrator_without_the_delegation_is_403(
    module_seeded_url, client, setup, conflicting
):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    payload = sunday(setup) if conflicting else body(setup)
    before = counts(module_seeded_url)

    response = create(
        client,
        sign_in(client, admin.username),
        with_override(payload, "outside_operating_hours"),
    )

    assert code_of(response) == FORBIDDEN
    # Only the sign-in's own events were written.
    assert counts(module_seeded_url)[:3] == before[:3]
    # Without the override the same Administrator creates the clean session.
    if not conflicting:
        assert code_of(create(client, sign_in(client, admin.username), payload)) == (
            CREATED
        )


def test_the_403_comes_before_the_body_is_read(module_seeded_url, client):
    # Unknown references would be 422 after the lock; the 403 comes first.
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    payload = body(student_id=uuid.uuid4(), subject_id=uuid.uuid4())

    response = create(
        client,
        sign_in(client, admin.username),
        with_override(payload, "outside_operating_hours"),
    )

    assert code_of(response) == FORBIDDEN


# --- G4: the reason and the codes --------------------------------------------------


@pytest.mark.parametrize("reason", [None, "", "   \t "])
def test_a_missing_or_blank_reason_is_reason_required(
    module_seeded_url, client, cookie, setup, reason
):
    override: dict[str, Any] = {"codes": ["outside_operating_hours"]}
    if reason is not None:
        override["reason"] = reason
    before = counts(module_seeded_url)

    response = create(client, cookie, sunday(setup) | {"override": override})

    assert field_errors(response) == [
        (["body", "override", "reason"], "reason_required")
    ]
    assert counts(module_seeded_url) == before


def test_the_reason_is_at_most_1000_characters_once_trimmed(
    module_seeded_url, client, cookie, setup
):
    too_long = create(
        client,
        cookie,
        with_override(sunday(setup), "outside_operating_hours", reason="a" * 1001),
    )
    exactly = create(
        client,
        cookie,
        with_override(
            sunday(setup), "outside_operating_hours", reason=f"  {'b' * 1000}  "
        ),
    )

    assert field_errors(too_long) == [
        (["body", "override", "reason"], "reason_too_long")
    ]
    assert code_of(exactly) == CREATED, exactly.text
    [row] = override_rows(module_seeded_url, exactly.json()["id"])
    assert row.reason == "b" * 1000


@pytest.mark.parametrize(
    ("codes", "error"),
    [
        ([], (["body", "override", "codes"], "codes_required")),
        (
            ["outside_operating_hours", "outside_operating_hours"],
            (["body", "override", "codes"], "duplicate_code"),
        ),
        (
            ["outside_operating_hours", "daily_limit_exceeded"],
            (["body", "override", "codes", 1], "code_unknown"),
        ),
        (["whatever"], (["body", "override", "codes", 0], "code_unknown")),
    ],
)
def test_invalid_codes_are_422(module_seeded_url, client, cookie, setup, codes, error):
    response = create(client, cookie, with_override(sunday(setup), *codes))

    assert field_errors(response) == [error]


# --- G5: a partial override ----------------------------------------------------


def test_an_unlisted_soft_conflict_refuses(module_seeded_url, client, cookie, setup):
    payload = sunday(setup, tutor_user_id=new_tutor(module_seeded_url))
    before = counts(module_seeded_url)

    response = create(client, cookie, with_override(payload, "tutor_not_qualified"))

    assert codes_of(response) == [
        ("outside_operating_hours", "soft"),
        ("tutor_not_qualified", "soft"),
    ]
    assert counts(module_seeded_url) == before


# --- G6: the table ---------------------------------------------------------------


def _sqlstate(error: DBAPIError) -> str | None:
    orig = error.orig
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if code is None and orig is not None and orig.__cause__ is not None:
        code = getattr(orig.__cause__, "sqlstate", None)
    return code


def test_override_rows_are_append_only_and_checked(
    module_seeded_url, client, cookie, owner, setup
):
    created = create(
        client, cookie, with_override(sunday(setup), "outside_operating_hours")
    ).json()
    [row] = override_rows(module_seeded_url, created["id"])

    for statement in (
        "UPDATE class_session_overrides SET reason = 'otra' WHERE id = :id",
        "DELETE FROM class_session_overrides WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as raised:
            run_sql(module_seeded_url, statement, {"id": str(row.id)})
        assert _sqlstate(raised.value) == "42501", raised.value

    insert = (
        "INSERT INTO class_session_overrides"
        " (class_session_id, codes, conflicts, reason, actor_user_id)"
        " VALUES (:session, :codes, '[]', :reason, :actor)"
    )
    for codes, reason, check in [
        (["student_overlap"], "x", "ck_class_session_overrides_codes_soft"),
        ([], "x", "ck_class_session_overrides_codes_soft"),
        (["center_closed"], "   ", "ck_class_session_overrides_reason_length"),
        (["center_closed"], "a" * 1001, "ck_class_session_overrides_reason_length"),
    ]:
        with pytest.raises(DBAPIError, match=check):
            run_sql(
                module_seeded_url,
                insert,
                {
                    "session": created["id"],
                    "codes": codes,
                    "reason": reason,
                    "actor": str(owner.id),
                },
            )


def test_overrides_of_a_session_list_oldest_first(module_seeded_url):
    # Later paths (#58) can add a second override to a session: the read
    # lists them by created_at (each written in its own transaction).
    owner = create_user(module_seeded_url, roles=(OWNER,), display_name=OWNER_NAME)
    session_id = existing_session(module_seeded_url, starts_at=at(MONDAY, "15:00"))
    conflict = scheduling.Conflict("center_closed", {"date": MONDAY.isoformat()})

    for reason in ("Primera", "Segunda"):

        async def _add(session: AsyncSession, reason: str = reason) -> None:
            await scheduling.record_session_override(
                session, session_id, [conflict], reason, actor_id=owner.id
            )

        in_uow(module_seeded_url, _add, actor_id=owner.id)

    async def _read(session: AsyncSession) -> Any:
        return await scheduling.get_class_session(session, session_id)

    detail = in_uow(module_seeded_url, _read)
    assert [o.reason for o in detail.overrides] == ["Primera", "Segunda"]
    assert detail.overrides[0].actor.display_name == OWNER_NAME
    assert [
        e.details["reason"] for e in override_events(module_seeded_url, session_id)
    ] == [
        "Primera",
        "Segunda",
    ]


def test_only_soft_conflicts_can_be_recorded(module_seeded_url):
    owner = create_user(module_seeded_url, roles=(OWNER,))
    session_id = existing_session(module_seeded_url, starts_at=at(MONDAY, "17:00"))
    hard = scheduling.Conflict("student_overlap", {})

    async def _add(session: AsyncSession) -> None:
        await scheduling.record_session_override(
            session, session_id, [hard], "x", actor_id=owner.id
        )

    with pytest.raises(ValueError, match="only soft"):
        in_uow(module_seeded_url, _add, actor_id=owner.id)
