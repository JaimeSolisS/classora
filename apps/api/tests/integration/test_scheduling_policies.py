"""Scheduling policy versions over HTTP and through the service (#61 C, D, E).

The tests of this module share one seeded database. Migration 0030's version
1 (effective from 2000-01-01) is always there; an autouse fixture deletes
every other version after each test (raw SQL, no audit event). Audit
assertions count only the events a test adds.
"""

import asyncio
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock as app_clock
from app.core.audit import AuditEvent
from app.core.errors import VersionConflictError
from app.modules.administration import service as administration
from app.modules.administration.errors import PolicyVersionInEffectError
from app.modules.administration.schemas import SchedulingPolicyCreate
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_CODES,
    STUDENT,
    TUTOR,
)
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, post
from tests.integration.conftest import run_sql
from tests.integration.guardian_support import delete
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow

POLICIES = "/api/v1/scheduling-policies"
VERSION_1_FROM = datetime(2000, 1, 1, tzinfo=UTC)
RULES = {
    "booking_notice_hours": 24,
    "change_notice_hours": 24,
    "response_hours": 48,
    "makeup_after_on_time": True,
    "makeup_after_late": False,
    "makeup_after_no_show": False,
    "makeup_window_days": 30,
}
API_ROOT = Path(__file__).resolve().parents[2]


def policy_path(policy_id: Any) -> str:
    return f"{POLICIES}/{policy_id}"


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value)


def version_1_id(url: str) -> uuid.UUID:
    [row] = run_sql(
        url,
        "SELECT id FROM scheduling_policies WHERE effective_from = :at",
        {"at": VERSION_1_FROM},
    )
    return row.id


def rows(url: str) -> list[Any]:
    return run_sql(
        url,
        "SELECT id, effective_from, change_notice_hours FROM scheduling_policies"
        " ORDER BY effective_from",
    )


def insert_version(url: str, effective_from: datetime, **changes: Any) -> uuid.UUID:
    values = {**RULES, **changes, "effective_from": effective_from}
    [row] = run_sql(
        url,
        "INSERT INTO scheduling_policies (effective_from, booking_notice_hours,"
        " change_notice_hours, response_hours, makeup_after_on_time,"
        " makeup_after_late, makeup_after_no_show, makeup_window_days)"
        " VALUES (:effective_from, :booking_notice_hours, :change_notice_hours,"
        " :response_hours, :makeup_after_on_time, :makeup_after_late,"
        " :makeup_after_no_show, :makeup_window_days) RETURNING id",
        values,
    )
    return row.id


def body(based_on_id: Any, **changes: Any) -> dict[str, Any]:
    return {"based_on_id": str(based_on_id), **RULES, **changes}


def policy_events(url: str) -> list[AuditEvent]:
    return events(url, record_type="scheduling_policies")


class EventCounter:
    """The scheduling policy events written since it was created."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.before = {event.id for event in policy_events(url)}

    def new(self) -> list[AuditEvent]:
        return [e for e in policy_events(self.url) if e.id not in self.before]


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture(autouse=True)
def only_version_1(module_seeded_url: str) -> Iterator[None]:
    yield
    run_sql(
        module_seeded_url,
        "DELETE FROM scheduling_policies WHERE effective_from <> :at",
        {"at": VERSION_1_FROM},
    )


@pytest.fixture
def cookie(client, owner):
    return sign_in(client, owner.username)


@pytest.fixture
def v1(module_seeded_url: str) -> uuid.UUID:
    return version_1_id(module_seeded_url)


@pytest.fixture
def counter(module_seeded_url: str) -> EventCounter:
    return EventCounter(module_seeded_url)


def future(days: int = 60) -> datetime:
    return (datetime.now(UTC) + timedelta(days=days)).replace(microsecond=0)


# --- C: the service ---------------------------------------------------------------


def policy_at(url: str, at: datetime) -> administration.SchedulingPolicyValues:
    async def call(session: AsyncSession) -> administration.SchedulingPolicyValues:
        return await administration.get_scheduling_policy(session, at)

    return in_uow(url, call)


def test_version_1_is_in_force_from_2000_and_now(module_seeded_url, v1):
    first = policy_at(module_seeded_url, VERSION_1_FROM)
    now = policy_at(module_seeded_url, datetime.now(UTC))

    assert first == now
    assert first.id == v1
    assert first.effective_from == VERSION_1_FROM
    assert (
        first.booking_notice_hours,
        first.change_notice_hours,
        first.response_hours,
        first.makeup_after_on_time,
        first.makeup_after_late,
        first.makeup_after_no_show,
        first.makeup_window_days,
    ) == tuple(RULES.values())


def test_an_upcoming_version_applies_from_its_instant(module_seeded_url, v1):
    at = future()
    upcoming = insert_version(module_seeded_url, at, change_notice_hours=12)

    assert policy_at(module_seeded_url, at - timedelta(microseconds=1)).id == v1
    in_force = policy_at(module_seeded_url, at)
    assert in_force.id == upcoming
    assert in_force.change_notice_hours == 12
    assert policy_at(module_seeded_url, at + timedelta(days=365)).id == upcoming

    run_sql(
        module_seeded_url,
        "DELETE FROM scheduling_policies WHERE id = :id",
        {"id": upcoming},
    )
    assert policy_at(module_seeded_url, at).id == v1


def test_the_latest_earlier_version_wins(module_seeded_url, v1):
    first = insert_version(module_seeded_url, datetime(2010, 1, 1, tzinfo=UTC))
    second = insert_version(module_seeded_url, datetime(2020, 1, 1, tzinfo=UTC))

    assert policy_at(module_seeded_url, datetime(2009, 12, 31, tzinfo=UTC)).id == v1
    assert policy_at(module_seeded_url, datetime(2015, 1, 1, tzinfo=UTC)).id == first
    assert policy_at(module_seeded_url, datetime(2026, 1, 1, tzinfo=UTC)).id == second


def test_the_lookup_takes_one_statement(module_seeded_url):
    async def call(session: AsyncSession) -> Any:
        return await administration.get_scheduling_policy(session, datetime.now(UTC))

    count, _policy = count_statements(module_seeded_url, call)

    assert count == 1


def test_a_naive_instant_raises(module_seeded_url):
    with pytest.raises(ValueError, match="aware"):
        policy_at(module_seeded_url, datetime(2026, 1, 1))


def test_the_evaluator_is_exported_by_the_service(module_seeded_url):
    policy = policy_at(module_seeded_url, datetime.now(UTC))
    starts = datetime(2026, 11, 10, 16, 0, tzinfo=UTC)

    outcome = administration.classify_change(
        policy,
        kind="cancellation",
        initiated_by="family",
        starts_at=starts,
        at=starts - timedelta(hours=30),
    )

    assert isinstance(outcome, administration.PolicyOutcome)
    assert outcome.classification == "on_time"
    assert outcome.policy_id == policy.id
    assert administration.violates_booking_notice(
        policy, starts_at=starts, at=starts - timedelta(hours=2)
    )
    assert (
        administration.default_respond_by(
            policy, offered_at=starts - timedelta(hours=10), first_starts_at=starts
        )
        == starts
    )


# --- D1: reading ------------------------------------------------------------------


@pytest.mark.parametrize("role", ROLE_CODES)
def test_every_role_reads_version_1_as_current(module_seeded_url, client, role, v1):
    person = create_user(module_seeded_url, roles=(role,))
    response = get(client, sign_in(client, person.username), POLICIES)

    assert response.status_code == 200, response.text
    data = response.json()
    current = data["current"]
    assert current["id"] == str(v1)
    assert instant(current["effective_from"]) == VERSION_1_FROM
    assert {name: current[name] for name in RULES} == RULES
    assert isinstance(current["created_at"], str)
    assert set(current) == {"id", "effective_from", "created_at", *RULES}
    assert data["upcoming"] == []
    assert data["history"] == []


def test_reading_needs_a_session(client):
    client.cookies.clear()

    assert code_of(client.get(POLICIES)) == (401, "authentication_required")


def test_reading_takes_two_statements(module_seeded_url):
    count, overview = count_statements(
        module_seeded_url, administration.get_scheduling_policy_overview
    )

    assert count <= 2
    assert overview.current.effective_from == VERSION_1_FROM


def test_the_overview_orders_upcoming_and_history(module_seeded_url, client, cookie):
    past_a = insert_version(module_seeded_url, datetime(2010, 1, 1, tzinfo=UTC))
    past_b = insert_version(module_seeded_url, datetime(2020, 1, 1, tzinfo=UTC))
    later = insert_version(module_seeded_url, future(90))
    sooner = insert_version(module_seeded_url, future(30))

    data = get(client, cookie, POLICIES).json()

    assert data["current"]["id"] == str(past_b)
    assert [item["id"] for item in data["upcoming"]] == [str(sooner), str(later)]
    assert [item["id"] for item in data["history"]] == [
        str(past_a),
        str(version_1_id(module_seeded_url)),
    ]


def test_history_holds_at_most_50_versions(module_seeded_url, client, cookie):
    start = datetime(2001, 1, 1, tzinfo=UTC)
    instants = [start + timedelta(days=30 * k) for k in range(52)]
    for at in instants:
        insert_version(module_seeded_url, at)

    data = get(client, cookie, POLICIES).json()

    assert instant(data["current"]["effective_from"]) == instants[-1]
    assert len(data["history"]) == 50
    assert [instant(item["effective_from"]) for item in data["history"]] == list(
        reversed(instants[1:-1])
    )


# --- D2: creating -----------------------------------------------------------------


def test_a_version_created_now_becomes_current(
    module_seeded_url, client, cookie, v1, counter, owner
):
    before = datetime.now(UTC)
    response = post(
        client, cookie, POLICIES, body(v1, change_notice_hours=12, response_hours=24)
    )

    assert response.status_code == 201, response.text
    created = response.json()
    assert before <= instant(created["effective_from"]) <= datetime.now(UTC)
    assert created["change_notice_hours"] == 12
    data = get(client, cookie, POLICIES).json()
    assert data["current"] == created
    assert [item["id"] for item in data["history"]] == [str(v1)]
    assert data["upcoming"] == []

    [event] = counter.new()
    assert event.action == "create"
    assert event.record_id == uuid.UUID(created["id"])
    assert event.actor_user_id == owner.id
    assert event.request_id == response.headers["X-Request-ID"]
    assert event.changes is not None
    assert {name: event.changes[name] for name in RULES} == {
        name: {"old": None, "new": value}
        for name, value in {
            **RULES,
            "change_notice_hours": 12,
            "response_hours": 24,
        }.items()
    }
    assert event.changes["effective_from"]["old"] is None
    assert instant(event.changes["effective_from"]["new"]) == instant(
        created["effective_from"]
    )


def test_an_upcoming_version_leaves_current_unchanged(
    module_seeded_url, client, cookie, v1
):
    at = future()
    response = post(
        client,
        cookie,
        POLICIES,
        body(v1, effective_from=at.isoformat(), change_notice_hours=12),
    )

    assert response.status_code == 201, response.text
    assert instant(response.json()["effective_from"]) == at
    data = get(client, cookie, POLICIES).json()
    assert data["current"]["id"] == str(v1)
    assert [item["id"] for item in data["upcoming"]] == [response.json()["id"]]


def test_the_next_version_is_based_on_the_upcoming_one(
    module_seeded_url, client, cookie, v1
):
    first = post(
        client, cookie, POLICIES, body(v1, effective_from=future(30).isoformat())
    ).json()

    stale = post(
        client, cookie, POLICIES, body(v1, effective_from=future(60).isoformat())
    )
    later = post(
        client,
        cookie,
        POLICIES,
        body(first["id"], effective_from=future(60).isoformat()),
    )

    assert code_of(stale) == (409, "version_conflict")
    assert later.status_code == 201, later.text
    assert len(get(client, cookie, POLICIES).json()["upcoming"]) == 2


def test_an_administrator_creates_a_version(module_seeded_url, client, v1):
    admin = create_user(module_seeded_url, roles=(ADMINISTRATOR,))
    cookie = sign_in(client, admin.username)

    response = post(client, cookie, POLICIES, body(v1))

    assert response.status_code == 201, response.text


def test_the_same_values_are_still_published(module_seeded_url, client, cookie, v1):
    response = post(client, cookie, POLICIES, body(v1))

    assert response.status_code == 201, response.text
    assert len(rows(module_seeded_url)) == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("booking_notice_hours", -1),
        ("booking_notice_hours", 337),
        ("change_notice_hours", -1),
        ("change_notice_hours", 337),
        ("response_hours", 0),
        ("response_hours", 169),
        ("makeup_window_days", 0),
        ("makeup_window_days", 366),
    ],
)
def test_a_value_out_of_range_is_422_on_its_field(
    module_seeded_url, client, cookie, v1, counter, field, value
):
    response = post(client, cookie, POLICIES, body(v1, **{field: value}))

    assert code_of(response) == (422, "validation_error")
    assert [e["path"] for e in response.json()["errors"]] == [["body", field]]
    assert len(rows(module_seeded_url)) == 1
    assert counter.new() == []


@pytest.mark.parametrize(
    "change",
    [
        {"effective_from": "2026-01-01T00:00:00"},  # naive
        {"version": 1},  # unknown field
        {"based_on_id": None},
        {"makeup_after_late": None},
    ],
)
def test_other_invalid_bodies_are_422(module_seeded_url, client, cookie, v1, change):
    response = post(client, cookie, POLICIES, {**body(v1), **change})

    assert code_of(response) == (422, "validation_error")
    assert len(rows(module_seeded_url)) == 1


def test_a_past_effective_from_is_422(module_seeded_url, client, cookie, v1, counter):
    past = datetime.now(UTC) - timedelta(minutes=1)

    response = post(client, cookie, POLICIES, body(v1, effective_from=past.isoformat()))

    assert code_of(response) == (422, "invalid_effective_from")
    assert len(rows(module_seeded_url)) == 1
    assert counter.new() == []


@pytest.mark.parametrize("offset", [timedelta(0), timedelta(days=-1)])
def test_an_effective_from_not_after_an_upcoming_version_is_422(
    module_seeded_url, client, cookie, offset
):
    at = future()
    upcoming = insert_version(module_seeded_url, at)

    response = post(
        client,
        cookie,
        POLICIES,
        body(upcoming, effective_from=(at + offset).isoformat()),
    )

    assert code_of(response) == (422, "invalid_effective_from")
    assert len(rows(module_seeded_url)) == 2


def test_now_is_not_after_an_upcoming_version(module_seeded_url, client, cookie):
    upcoming = insert_version(module_seeded_url, future())

    response = post(client, cookie, POLICIES, body(upcoming))

    assert code_of(response) == (422, "invalid_effective_from")


def test_a_stale_based_on_id_is_409(module_seeded_url, client, cookie, v1, counter):
    insert_version(module_seeded_url, datetime(2020, 1, 1, tzinfo=UTC))

    stale = post(client, cookie, POLICIES, body(v1))
    unknown = post(client, cookie, POLICIES, body(uuid.uuid7()))

    assert code_of(stale) == (409, "version_conflict")
    assert code_of(unknown) == (409, "version_conflict")
    assert len(rows(module_seeded_url)) == 2
    assert counter.new() == []


def test_the_version_is_checked_before_the_effective_from(
    module_seeded_url, client, cookie
):
    response = post(
        client,
        cookie,
        POLICIES,
        body(uuid.uuid7(), effective_from="2001-01-01T00:00:00Z"),
    )

    assert code_of(response) == (409, "version_conflict")


HOLD_SECONDS = 0.5


def test_two_concurrent_creates_on_the_same_version_one_wins(module_seeded_url, v1):
    from app.core.db import create_sessionmaker
    from app.core.uow import UnitOfWork
    from tests.integration.conftest import short_lived_engine

    def create_body(hours: int) -> SchedulingPolicyCreate:
        return SchedulingPolicyCreate.model_validate(
            body(v1, change_notice_hours=hours)
        )

    async def create(engine: Any, hours: int, hold: asyncio.Event | None) -> str:
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                await administration.create_scheduling_policy(
                    uow.session, create_body(hours)
                )
                if hold is not None:
                    hold.set()
                    await asyncio.sleep(HOLD_SECONDS)
        except VersionConflictError:
            return "version_conflict"
        finally:
            if hold is not None:
                hold.set()
        return "ok"

    async def run() -> list[str]:
        engine_a = short_lived_engine(module_seeded_url)
        engine_b = short_lived_engine(module_seeded_url)
        created = asyncio.Event()

        async def second() -> str:
            await created.wait()
            return await create(engine_b, 6, None)

        try:
            return list(await asyncio.gather(create(engine_a, 12, created), second()))
        finally:
            await engine_a.dispose()
            await engine_b.dispose()

    assert asyncio.run(run()) == ["ok", "version_conflict"]
    assert [row.change_notice_hours for row in rows(module_seeded_url)] == [24, 12]


# --- D3: deleting -----------------------------------------------------------------


def test_an_upcoming_version_is_deleted(
    module_seeded_url, client, cookie, v1, counter, owner
):
    created = post(
        client, cookie, POLICIES, body(v1, effective_from=future().isoformat())
    )
    policy_id = created.json()["id"]

    response = delete(client, cookie, policy_path(policy_id))

    assert response.status_code == 204, response.text
    assert [row.id for row in rows(module_seeded_url)] == [v1]
    assert get(client, cookie, POLICIES).json()["upcoming"] == []
    assert [(e.action, str(e.record_id)) for e in counter.new()] == [
        ("create", policy_id),
        ("delete", policy_id),
    ]
    deleted = counter.new()[1]
    assert deleted.actor_user_id == owner.id
    assert deleted.request_id == response.headers["X-Request-ID"]


def test_a_version_in_force_or_past_is_409(
    module_seeded_url, client, cookie, v1, counter
):
    current = post(client, cookie, POLICIES, body(v1)).json()["id"]
    before = [event.id for event in counter.new()]

    for policy_id in (current, v1):
        response = delete(client, cookie, policy_path(policy_id))
        assert code_of(response) == (409, "policy_version_in_effect")

    assert len(rows(module_seeded_url)) == 2
    assert [event.id for event in counter.new()] == before


def test_version_1_is_never_deleted(module_seeded_url, client, cookie, v1):
    response = delete(client, cookie, policy_path(v1))

    assert code_of(response) == (409, "policy_version_in_effect")
    assert [row.id for row in rows(module_seeded_url)] == [v1]


def test_an_unknown_version_is_404(module_seeded_url, client, cookie, counter):
    response = delete(client, cookie, policy_path(uuid.uuid7()))

    assert code_of(response) == (404, "not_found")
    assert counter.new() == []


def test_the_clock_decides_what_is_upcoming(clock, module_seeded_url):
    at = datetime(2030, 1, 1, tzinfo=UTC)
    upcoming = insert_version(module_seeded_url, at)

    async def overview(session: AsyncSession) -> Any:
        return await administration.get_scheduling_policy_overview(session)

    async def remove(session: AsyncSession) -> None:
        await administration.delete_scheduling_policy(session, upcoming)

    clock.set(at - timedelta(microseconds=1))
    assert [item.id for item in in_uow(module_seeded_url, overview).upcoming] == [
        upcoming
    ]

    clock.set(at)
    assert app_clock.now() == at
    assert in_uow(module_seeded_url, overview).current.id == upcoming
    with pytest.raises(PolicyVersionInEffectError):
        in_uow(module_seeded_url, remove)


# --- authorization ----------------------------------------------------------------


@pytest.mark.parametrize(
    "role", [ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR, STUDENT, GUARDIAN]
)
def test_only_owners_and_administrators_write(
    module_seeded_url, client, role, v1, counter
):
    upcoming = insert_version(module_seeded_url, future())
    person = create_user(module_seeded_url, roles=(role,))
    cookie = sign_in(client, person.username)

    created = post(client, cookie, POLICIES, body(upcoming))
    deleted = delete(client, cookie, policy_path(upcoming))

    assert code_of(created) == (403, "permission_denied")
    assert code_of(deleted) == (403, "permission_denied")
    assert len(rows(module_seeded_url)) == 2
    assert counter.new() == []


# --- E2: append-only --------------------------------------------------------------


def test_no_code_updates_a_policy_version():
    """No ``UPDATE`` of ``scheduling_policies`` and no attribute assignment on
    a loaded version anywhere in ``app/`` (#61 E2)."""
    offenders = []
    for path in (API_ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"update\(\s*SchedulingPolicy\b", text) or re.search(
            r"UPDATE\s+scheduling_policies", text, re.IGNORECASE
        ):
            offenders.append(str(path))
    assert offenders == []
    source = (API_ROOT / "app/modules/administration/policy_versions.py").read_text(
        encoding="utf-8"
    )
    assert not re.search(r"\brow\.\w+\s*=", source)
    assert not re.search(r"\blatest\.\w+\s*=", source)
