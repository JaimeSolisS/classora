"""The last-active-Owner guard inside the identity service (#31 C; ADR-0007 D4).

Every test calls ``service.change_user_state`` / ``service.remove_user_role``
directly in its own Unit of Work, on a fresh database, so the number of
Owners is exactly what the test creates.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.identity import service as identity
from app.modules.identity.errors import LastActiveOwnerError
from app.modules.identity.permissions import (
    ACTIVE,
    ADMINISTRATOR,
    ARCHIVED,
    OWNER,
    PENDING,
    SUSPENDED,
    TUTOR,
)
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import in_uow, live_count, signed_in

LOCK_SQL = "pg_advisory_xact_lock"


@pytest.fixture
def client(seeded_url: str) -> Iterator[TestClient]:
    with auth_client(seeded_url) as test_client:
        yield test_client


def owner(url: str, state: str = ACTIVE, **overrides: Any) -> CreatedUser:
    return create_user(url, roles=(OWNER,), state=state, **overrides)


def suspend(url: str, user_id: uuid.UUID) -> bool:
    return in_uow(
        url,
        lambda s: identity.change_user_state(s, user_id=user_id, new_state=SUSPENDED),
    )


def archive(url: str, user_id: uuid.UUID) -> bool:
    return in_uow(
        url,
        lambda s: identity.change_user_state(s, user_id=user_id, new_state=ARCHIVED),
    )


def remove_owner(url: str, user_id: uuid.UUID) -> bool:
    return in_uow(
        url, lambda s: identity.remove_user_role(s, user_id=user_id, role=OWNER)
    )


GUARDED_CHANGES = {"suspend": suspend, "archive": archive, "remove_owner": remove_owner}


def snapshot(url: str, user_id: uuid.UUID) -> tuple[Any, ...]:
    [user] = run_sql(
        url, "SELECT state, version FROM users WHERE id = :id", {"id": user_id}
    )
    roles = run_sql(
        url,
        "SELECT r.code FROM user_roles ur JOIN roles r ON r.id = ur.role_id"
        " WHERE ur.user_id = :id ORDER BY r.code",
        {"id": user_id},
    )
    [audit] = run_sql(url, "SELECT count(*) AS n FROM audit_events")
    return (user.state, user.version, [r.code for r in roles], audit.n)


def state_of(url: str, user_id: uuid.UUID) -> str:
    [row] = run_sql(url, "SELECT state FROM users WHERE id = :id", {"id": user_id})
    state: str = row.state
    return state


def active_owner_count(url: str) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM users u JOIN user_roles ur ON ur.user_id = u.id"
        " JOIN roles r ON r.id = ur.role_id WHERE r.code = 'owner'"
        " AND u.state = 'active'",
    )
    count: int = row.n
    return count


# --- One active Owner -----------------------------------------------------------------


@pytest.mark.parametrize("change", GUARDED_CHANGES)
def test_the_only_active_owner_is_guarded_and_nothing_changes(
    seeded_url, client, change
):
    sole = owner(seeded_url)
    cookie_a = signed_in(client, sole.username)
    signed_in(client, sole.username)
    assert cookie_a.value
    before = snapshot(seeded_url, sole.id)
    assert live_count(seeded_url, sole.id) == 2

    with pytest.raises(LastActiveOwnerError):
        GUARDED_CHANGES[change](seeded_url, sole.id)

    assert snapshot(seeded_url, sole.id) == before  # state, roles, no audit row
    assert live_count(seeded_url, sole.id) == 2


def test_an_owner_who_also_holds_another_role_is_still_guarded(seeded_url):
    sole = create_user(seeded_url, roles=(OWNER, TUTOR))

    with pytest.raises(LastActiveOwnerError):
        suspend(seeded_url, sole.id)
    # Removing another role is never guarded.
    assert in_uow(
        seeded_url,
        lambda s: identity.remove_user_role(s, user_id=sole.id, role=TUTOR),
    )


# --- Two active Owners ----------------------------------------------------------------


def test_with_two_active_owners_one_can_be_suspended_then_the_other_is_guarded(
    seeded_url,
):
    first, second = owner(seeded_url), owner(seeded_url)

    assert suspend(seeded_url, first.id) is True

    assert state_of(seeded_url, first.id) == SUSPENDED
    for change in GUARDED_CHANGES.values():
        with pytest.raises(LastActiveOwnerError):
            change(seeded_url, second.id)
    assert state_of(seeded_url, second.id) == ACTIVE


@pytest.mark.parametrize("change", GUARDED_CHANGES)
def test_with_two_active_owners_each_guarded_change_works_once(seeded_url, change):
    first, second = owner(seeded_url), owner(seeded_url)

    assert GUARDED_CHANGES[change](seeded_url, first.id) is True

    assert active_owner_count(seeded_url) == 1
    with pytest.raises(LastActiveOwnerError):
        GUARDED_CHANGES[change](seeded_url, second.id)


# --- One active and one suspended Owner -----------------------------------------------


def test_a_suspended_owner_does_not_count_but_is_never_guarded(seeded_url):
    active = owner(seeded_url)
    suspended = owner(seeded_url, state=SUSPENDED)

    for change in GUARDED_CHANGES.values():
        with pytest.raises(LastActiveOwnerError):
            change(seeded_url, active.id)

    assert archive(seeded_url, suspended.id) is True
    assert remove_owner(seeded_url, suspended.id) is True
    assert state_of(seeded_url, suspended.id) == ARCHIVED
    assert state_of(seeded_url, active.id) == ACTIVE


# --- Pending Owner invitations --------------------------------------------------------


def test_a_pending_owner_invitation_is_never_guarded(seeded_url):
    # No active Owner at all: archiving the invitation still works.
    invited = owner(seeded_url, state=PENDING, password_hash=None)

    assert archive(seeded_url, invited.id) is True
    assert state_of(seeded_url, invited.id) == ARCHIVED


def test_a_pending_owner_can_lose_the_owner_role(seeded_url):
    invited = owner(seeded_url, state=PENDING, password_hash=None)

    assert remove_owner(seeded_url, invited.id) is True


# --- The lock is taken only by guarded changes ----------------------------------------


def run_recording[T](
    url: str, call: Callable[[AsyncSession], Awaitable[T]]
) -> tuple[T | BaseException, list[str]]:
    """Run ``call`` in a Unit of Work; return its result (or the exception)
    and every SQL statement it ran."""
    seen: list[str] = []

    async def _run() -> T | BaseException:
        engine = short_lived_engine(url)

        def record(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
            seen.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                return await call(uow.session)
        except LastActiveOwnerError as exc:
            return exc
        finally:
            await engine.dispose()

    return asyncio.run(_run()), seen


def took_lock(statements: list[str]) -> bool:
    return any(LOCK_SQL in statement for statement in statements)


def test_guarded_changes_take_the_lock(seeded_url):
    first, second = owner(seeded_url), owner(seeded_url)

    _, suspending = run_recording(
        seeded_url,
        lambda s: identity.change_user_state(s, user_id=first.id, new_state=SUSPENDED),
    )
    result, removing = run_recording(
        seeded_url,
        lambda s: identity.remove_user_role(s, user_id=second.id, role=OWNER),
    )

    assert took_lock(suspending)
    assert took_lock(removing)
    assert isinstance(result, LastActiveOwnerError)


@pytest.mark.parametrize(
    ("roles", "state", "call"),
    [
        ((TUTOR,), ACTIVE, ("state", SUSPENDED)),
        ((TUTOR,), ACTIVE, ("state", ARCHIVED)),
        ((ADMINISTRATOR,), ACTIVE, ("remove", ADMINISTRATOR)),
        ((OWNER,), SUSPENDED, ("state", ACTIVE)),
        ((OWNER,), SUSPENDED, ("state", ARCHIVED)),
        ((OWNER,), SUSPENDED, ("remove", OWNER)),
        ((OWNER,), PENDING, ("state", ARCHIVED)),
        ((OWNER,), PENDING, ("state", ACTIVE)),
        ((TUTOR,), ACTIVE, ("add", OWNER)),
    ],
)
def test_other_transitions_and_roles_never_take_the_lock(
    seeded_url, roles, state, call
):
    target = create_user(seeded_url, roles=roles, state=state)
    kind, value = call

    def act(s: AsyncSession) -> Awaitable[bool]:
        if kind == "state":
            return identity.change_user_state(s, user_id=target.id, new_state=value)
        if kind == "remove":
            return identity.remove_user_role(s, user_id=target.id, role=value)
        return identity.add_user_role(s, user_id=target.id, role=value)

    result, statements = run_recording(seeded_url, act)

    assert result is True
    assert not took_lock(statements)


# --- Concurrency ----------------------------------------------------------------------


async def _suspend_holding_the_commit(
    url: str, user_id: uuid.UUID, barrier: asyncio.Barrier
) -> str:
    """Suspend ``user_id``, then wait (at most 2 s) for the other task at the
    barrier before committing. With the lock, the other task is still waiting
    for it, so the barrier times out and this one commits first."""
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await identity.change_user_state(
                uow.session, user_id=user_id, new_state=SUSPENDED
            )
            try:
                async with asyncio.timeout(2):
                    await barrier.wait()
            except TimeoutError, asyncio.BrokenBarrierError:
                pass
        return "suspended"
    except LastActiveOwnerError:
        return "last_active_owner"
    finally:
        await engine.dispose()


def test_two_concurrent_suspensions_of_the_last_two_owners(seeded_url):
    first, second = owner(seeded_url), owner(seeded_url)

    async def _both() -> list[str]:
        barrier = asyncio.Barrier(2)
        return list(
            await asyncio.gather(
                _suspend_holding_the_commit(seeded_url, first.id, barrier),
                _suspend_holding_the_commit(seeded_url, second.id, barrier),
            )
        )

    outcomes = asyncio.run(_both())

    assert sorted(outcomes) == ["last_active_owner", "suspended"]
    assert active_owner_count(seeded_url) == 1
    states = {state_of(seeded_url, first.id), state_of(seeded_url, second.id)}
    assert states == {ACTIVE, SUSPENDED}
