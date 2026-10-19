"""Delegation grants and revocations (ADR-0007, D4; M2-02 G3; #170 B)."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.modules.identity import service
from app.modules.identity.models import Permission, User, UserDelegation, UserRole
from app.modules.identity.permissions import (
    ADMINISTRATOR,
    DELEGABLE_PERMISSIONS,
    OWNER,
    ROLE_PERMISSIONS,
    TUTOR,
)
from tests.audit.assertions import audit_events_for
from tests.factories.identity import persist_user
from tests.integration.conftest import run_sql_async, short_lived_engine

pytestmark = pytest.mark.anyio

ADMIN_ROLE_ASSIGN = "user.admin_role.assign"


@pytest.fixture
async def session(seeded_url: str) -> AsyncIterator[AsyncSession]:
    engine = short_lived_engine(seeded_url)
    try:
        async with create_sessionmaker(engine)() as db_session:
            yield db_session
            await db_session.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def owner(session: AsyncSession) -> User:
    user: User = await persist_user(session, roles=[OWNER])
    return user


@pytest.fixture
async def admin(session: AsyncSession) -> User:
    user: User = await persist_user(session, roles=[ADMINISTRATOR])
    return user


async def delegations(session: AsyncSession) -> list[tuple[uuid.UUID, str, uuid.UUID]]:
    rows = await session.execute(
        select(
            UserDelegation.user_id, Permission.code, UserDelegation.granted_by_user_id
        )
        .join(Permission, Permission.id == UserDelegation.permission_id)
        .order_by(Permission.code)
    )
    return [tuple(row) for row in rows]  # type: ignore[misc]


# --- Granting and revoking --------------------------------------------------------


async def test_owner_grants_admin_role_assign_to_an_administrator(
    session, owner, admin
):
    before = await service.effective_permissions(session, admin.id)
    assert not service.may_assign_role(before, ADMINISTRATOR)

    await service.grant_delegation(
        session, actor_id=owner.id, user_id=admin.id, permission=ADMIN_ROLE_ASSIGN
    )

    after = await service.effective_permissions(session, admin.id)
    assert after == before | {ADMIN_ROLE_ASSIGN}
    assert service.may_assign_role(after, ADMINISTRATOR)
    assert not service.may_assign_role(after, OWNER)
    assert await delegations(session) == [(admin.id, ADMIN_ROLE_ASSIGN, owner.id)]


@pytest.mark.parametrize("permission", sorted(DELEGABLE_PERMISSIONS))
async def test_every_delegable_permission_can_be_granted(
    session, owner, admin, permission
):
    await service.grant_delegation(
        session, actor_id=owner.id, user_id=admin.id, permission=permission
    )

    assert permission in await service.effective_permissions(session, admin.id)


async def test_granting_twice_is_a_no_op(session, owner, admin):
    for _ in range(2):
        await service.grant_delegation(
            session, actor_id=owner.id, user_id=admin.id, permission="payroll.finalize"
        )

    assert await delegations(session) == [(admin.id, "payroll.finalize", owner.id)]


async def test_revoke_removes_the_delegation(session, owner, admin):
    await service.grant_delegation(
        session, actor_id=owner.id, user_id=admin.id, permission="schedule.override"
    )

    await service.revoke_delegation(
        session, actor_id=owner.id, user_id=admin.id, permission="schedule.override"
    )

    assert await delegations(session) == []
    assert (
        await service.effective_permissions(session, admin.id)
        == (ROLE_PERMISSIONS[ADMINISTRATOR])
    )


async def test_revoking_a_missing_delegation_is_a_no_op(session, owner, admin):
    await service.revoke_delegation(
        session, actor_id=owner.id, user_id=admin.id, permission="payroll.finalize"
    )

    assert await delegations(session) == []


async def test_a_suspended_administrator_can_receive_a_delegation(session, owner):
    suspended = await persist_user(session, roles=[ADMINISTRATOR], state="suspended")

    await service.grant_delegation(
        session, actor_id=owner.id, user_id=suspended.id, permission="payroll.finalize"
    )

    assert len(await delegations(session)) == 1
    # Not active, so it has no effective permissions until reactivated.
    assert await service.effective_permissions(session, suspended.id) == frozenset()


async def test_delegation_stops_counting_when_the_administrator_role_goes(
    session, owner, admin
):
    await service.grant_delegation(
        session, actor_id=owner.id, user_id=admin.id, permission="payroll.finalize"
    )
    # #31 removes the role; the delegation row may outlive it until cleanup.
    # Through the ORM: a bulk delete on an audited table is refused (#22 C7).
    for user_role in await session.scalars(
        select(UserRole).where(UserRole.user_id == admin.id)
    ):
        await session.delete(user_role)
    await session.flush()

    assert await service.effective_permissions(session, admin.id) == frozenset()
    assert len(await delegations(session)) == 1


# --- Refusals: a typed error and nothing changes ------------------------------------


async def assert_refused(
    session, error, *, actor_id, user_id, permission, grant_only=False
):
    """Both operations (or only the grant) raise ``error`` and change nothing."""
    before = await delegations(session)
    operations = [service.grant_delegation]
    if not grant_only:
        operations.append(service.revoke_delegation)
    for operation in operations:
        with pytest.raises(error):
            await operation(
                session, actor_id=actor_id, user_id=user_id, permission=permission
            )
    assert await delegations(session) == before


async def test_an_administrator_cannot_delegate(session, owner, admin):
    # Even an Administrator who holds every delegable permission.
    for permission in DELEGABLE_PERMISSIONS:
        await service.grant_delegation(
            session, actor_id=owner.id, user_id=admin.id, permission=permission
        )
    other = await persist_user(session, roles=[ADMINISTRATOR])

    await assert_refused(
        session,
        service.DelegationForbiddenError,
        actor_id=admin.id,
        user_id=other.id,
        permission="payroll.finalize",
    )


@pytest.mark.parametrize("state", ["pending", "suspended", "archived"])
async def test_a_non_active_owner_cannot_delegate(session, admin, state):
    owner = await persist_user(session, roles=[OWNER], state=state)

    await assert_refused(
        session,
        service.DelegationForbiddenError,
        actor_id=owner.id,
        user_id=admin.id,
        permission="payroll.finalize",
    )


async def test_an_unknown_actor_cannot_delegate(session, admin):
    await assert_refused(
        session,
        service.DelegationForbiddenError,
        actor_id=uuid.uuid7(),
        user_id=admin.id,
        permission="payroll.finalize",
    )


@pytest.mark.parametrize(
    "permission",
    ["user.delegation.manage", "user.owner_role.assign", "user.view", "no.such_code"],
)
async def test_non_delegable_or_unknown_permissions_are_refused(
    session, owner, admin, permission
):
    await assert_refused(
        session,
        service.PermissionNotDelegableError,
        actor_id=owner.id,
        user_id=admin.id,
        permission=permission,
    )


# #170 B2: only a grant needs a non-archived Administrator; a revocation works
# on any existing user (tested below).


async def test_target_without_the_administrator_role_is_refused(session, owner):
    tutor = await persist_user(session, roles=[TUTOR])

    await assert_refused(
        session,
        service.DelegationTargetInvalidError,
        actor_id=owner.id,
        user_id=tutor.id,
        permission="payroll.finalize",
        grant_only=True,
    )


async def test_owner_target_is_refused(session, owner):
    other_owner = await persist_user(session, roles=[OWNER])

    await assert_refused(
        session,
        service.DelegationTargetInvalidError,
        actor_id=owner.id,
        user_id=other_owner.id,
        permission="payroll.finalize",
        grant_only=True,
    )


async def test_archived_administrator_is_refused(session, owner):
    archived = await persist_user(session, roles=[ADMINISTRATOR], state="archived")

    await assert_refused(
        session,
        service.DelegationTargetInvalidError,
        actor_id=owner.id,
        user_id=archived.id,
        permission="payroll.finalize",
        grant_only=True,
    )


async def test_unknown_target_is_refused(session, owner):
    await assert_refused(
        session,
        service.DelegationTargetInvalidError,
        actor_id=owner.id,
        user_id=uuid.uuid7(),
        permission="payroll.finalize",
    )


async def test_errors_have_stable_codes():
    assert service.DelegationForbiddenError.code == "delegation_forbidden"
    assert service.DelegationForbiddenError.status == 403
    assert service.PermissionNotDelegableError.code == "permission_not_delegable"
    assert service.DelegationTargetInvalidError.code == "delegation_target_invalid"


# --- #170 B2: revoking from an archived or former Administrator ------------------


async def test_revoke_from_an_archived_administrator(session, owner):
    archived = await persist_user(
        session,
        roles=[ADMINISTRATOR],
        state="archived",
        delegations=["payroll.finalize"],
        granted_by=owner.id,
    )

    await service.revoke_delegation(
        session, actor_id=owner.id, user_id=archived.id, permission="payroll.finalize"
    )
    # Absent now: a no-op.
    await service.revoke_delegation(
        session, actor_id=owner.id, user_id=archived.id, permission="payroll.finalize"
    )

    assert await delegations(session) == []


async def test_revoke_from_a_former_administrator(session, owner):
    # A row left by older data: the user no longer holds administrator.
    former = await persist_user(
        session,
        roles=[TUTOR],
        delegations=["schedule.override", "payroll.finalize"],
        granted_by=owner.id,
    )

    for permission in ("schedule.override", "payroll.finalize", "payroll.finalize"):
        await service.revoke_delegation(
            session, actor_id=owner.id, user_id=former.id, permission=permission
        )

    assert await delegations(session) == []


@pytest.mark.parametrize("state", ["active", "archived"])
async def test_revoke_still_needs_an_owner_and_a_delegable_code(session, state):
    target = await persist_user(
        session,
        roles=[ADMINISTRATOR],
        state=state,
        delegations=["payroll.finalize"],
    )
    admin = await persist_user(session, roles=[ADMINISTRATOR])
    owner = await persist_user(session, roles=[OWNER])

    with pytest.raises(service.DelegationForbiddenError):
        await service.revoke_delegation(
            session, actor_id=admin.id, user_id=target.id, permission="payroll.finalize"
        )
    with pytest.raises(service.PermissionNotDelegableError):
        await service.revoke_delegation(
            session, actor_id=owner.id, user_id=target.id, permission="user.view"
        )
    assert len(await delegations(session)) == 1


# --- #170 B1: removing the Administrator role removes the delegations -------------


async def test_removing_administrator_deletes_every_delegation(session, owner):
    admin = await persist_user(
        session,
        roles=[ADMINISTRATOR, TUTOR],
        delegations=["payroll.finalize", ADMIN_ROLE_ASSIGN],
        granted_by=owner.id,
    )
    other = await persist_user(
        session,
        roles=[ADMINISTRATOR],
        delegations=["payroll.finalize"],
        granted_by=owner.id,
    )
    await session.flush()
    rows_before = {
        row.id
        for row in await session.scalars(
            select(UserDelegation).where(UserDelegation.user_id == admin.id)
        )
    }

    assert await service.remove_user_role(session, user_id=admin.id, role=ADMINISTRATOR)

    assert await delegations(session) == [(other.id, "payroll.finalize", owner.id)]
    deleted = await audit_events_for(
        session, record_type="user_delegations", action="delete"
    )
    assert {event.record_id for event in deleted} == rows_before
    assert (
        await service.effective_permissions(session, admin.id)
        == ROLE_PERMISSIONS[TUTOR]
    )


@pytest.mark.parametrize("role", [TUTOR, OWNER])
async def test_removing_another_role_keeps_the_delegations(session, owner, role):
    # A second active Owner, so the guard allows removing owner.
    admin = await persist_user(
        session,
        roles=[ADMINISTRATOR, role],
        delegations=["payroll.finalize"],
        granted_by=owner.id,
    )

    assert await service.remove_user_role(session, user_id=admin.id, role=role)

    assert await delegations(session) == [(admin.id, "payroll.finalize", owner.id)]


# --- #22 QA note: concurrent grants, and only the unique violation is a no-op ------


async def test_two_concurrent_grants_of_one_delegation_leave_one_row(seeded_url):
    setup = short_lived_engine(seeded_url)
    first_engine = short_lived_engine(seeded_url)
    second_engine = short_lived_engine(seeded_url)
    try:
        async with create_sessionmaker(setup)() as db, db.begin():
            owner = await persist_user(db, roles=[OWNER])
            admin = await persist_user(db, roles=[ADMINISTRATOR])
            owner_id, admin_id = owner.id, admin.id

        async with (
            create_sessionmaker(first_engine)() as first,
            create_sessionmaker(second_engine)() as second,
        ):
            # The first grant inserts (uncommitted); the second sees no row,
            # inserts too and waits on the unique index.
            await service.grant_delegation(
                first,
                actor_id=owner_id,
                user_id=admin_id,
                permission="payroll.finalize",
            )
            waiting = asyncio.create_task(
                service.grant_delegation(
                    second,
                    actor_id=owner_id,
                    user_id=admin_id,
                    permission="payroll.finalize",
                )
            )
            await asyncio.sleep(0.5)
            assert not waiting.done()
            await first.commit()
            # The unique violation inside the savepoint: a no-op, and the
            # second transaction is still usable.
            await asyncio.wait_for(waiting, timeout=10)
            assert await second.scalar(select(func.count(UserDelegation.id))) == 1
            await second.commit()
    finally:
        for engine in (setup, first_engine, second_engine):
            await engine.dispose()

    rows = await run_sql_async(
        seeded_url,
        "SELECT count(*) FROM user_delegations WHERE user_id = :id",
        {"id": admin_id},
    )
    assert rows[0][0] == 1


async def test_another_integrity_error_is_not_swallowed(
    session, owner, admin, monkeypatch
):
    # A permission id that exists nowhere: a foreign-key violation, which
    # must not pass for "already granted".
    async def unknown_permission(*args, **kwargs):
        return uuid.uuid7()

    monkeypatch.setattr(service, "_check_delegation", unknown_permission)

    with pytest.raises(IntegrityError):
        await service.grant_delegation(
            session, actor_id=owner.id, user_id=admin.id, permission="payroll.finalize"
        )


# --- Never commits ------------------------------------------------------------------


async def test_grant_never_commits(seeded_url):
    engine = short_lived_engine(seeded_url)
    try:
        async with create_sessionmaker(engine)() as session:
            owner = await persist_user(session, roles=[OWNER])
            admin = await persist_user(session, roles=[ADMINISTRATOR])
            await service.grant_delegation(
                session,
                actor_id=owner.id,
                user_id=admin.id,
                permission="payroll.finalize",
            )
            assert await session.scalar(select(func.count(UserDelegation.id))) == 1
            assert session.in_transaction()
            await session.rollback()
    finally:
        await engine.dispose()

    rows = await run_sql_async(seeded_url, "SELECT count(*) FROM user_delegations")
    assert rows[0][0] == 0
