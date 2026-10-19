"""The first audited models: users, role assignments, delegations (M2-04 E1)."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditEvent
from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.identity import service
from app.modules.identity.models import UserDelegation, UserRole
from app.modules.identity.permissions import ADMINISTRATOR, OWNER, TUTOR
from app.seeds import runner
from tests.audit.assertions import assert_no_secret_in_audit, audit_events_for
from tests.factories.identity import factory_password_hash, persist_user
from tests.integration.conftest import settings_for, short_lived_engine

pytestmark = pytest.mark.anyio


@pytest.fixture
async def sessionmaker(
    seeded_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = short_lived_engine(seeded_url)
    try:
        yield create_sessionmaker(engine)
    finally:
        await engine.dispose()


async def all_events(sessionmaker) -> list[AuditEvent]:
    async with sessionmaker() as session:
        return await audit_events_for(session)


async def test_reference_seeds_write_no_audit_rows(seeded_url):
    # Roles, permissions and role permissions are not audited (ADR-0007).
    engine = runner.create_seed_engine(settings_for(seeded_url))
    try:
        await runner.run_reference_seeds(engine)  # a rerun, too
        async with create_sessionmaker(engine)() as session:
            assert await session.scalar(select(func.count(AuditEvent.id))) == 0
    finally:
        await engine.dispose()


async def test_creating_a_user_writes_a_create_without_the_hash(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        user = await persist_user(uow.session, username="ana.audit", roles=[TUTOR])

    async with sessionmaker() as session:
        [created] = await audit_events_for(
            session, record_type="users", record_id=user.id
        )
        [role] = await audit_events_for(session, record_type="user_roles")
        await assert_no_secret_in_audit(session, factory_password_hash(), "argon2")
    assert created.action == "create"
    assert created.changes is not None
    assert "password_hash" not in created.changes
    # email and phone (#31) are audited like the other columns.
    assert set(created.changes) == {
        "username",
        "display_name",
        "state",
        "email",
        "phone",
    }
    assert created.changes["username"] == {"old": None, "new": "ana.audit"}
    assert created.changes["state"] == {"old": None, "new": "active"}
    assert role.action == "create"
    assert role.changes is not None
    assert role.changes["user_id"] == {"old": None, "new": str(user.id)}
    assert set(role.changes) == {"user_id", "role_id"}


async def test_a_state_change_is_an_update(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        user = await persist_user(uow.session, roles=[TUTOR])
    async with UnitOfWork(sessionmaker) as uow:
        loaded = await uow.session.get_one(type(user), user.id)
        loaded.state = "suspended"

    async with sessionmaker() as session:
        [event] = await audit_events_for(
            session, record_type="users", record_id=user.id, action="update"
        )
    # version and updated_at changed too; they are left out.
    assert event.changes == {"state": {"old": "active", "new": "suspended"}}


async def test_changing_only_the_password_hash_writes_no_event(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        user = await persist_user(uow.session)
    async with UnitOfWork(sessionmaker) as uow:
        loaded = await uow.session.get_one(type(user), user.id)
        loaded.password_hash = service.hash_password("another-long-password")

    async with sessionmaker() as session:
        events = await audit_events_for(session, record_id=user.id)
        await assert_no_secret_in_audit(session, "argon2", "another-long-password")
    assert [e.action for e in events] == ["create"]


async def test_removing_a_role_is_a_delete(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        user = await persist_user(uow.session, roles=[TUTOR])
    async with UnitOfWork(sessionmaker) as uow:
        user_role = await uow.session.scalar(
            select(UserRole).where(UserRole.user_id == user.id)
        )
        assert user_role is not None
        role_id, assignment_id = user_role.role_id, user_role.id
        await uow.session.delete(user_role)

    async with sessionmaker() as session:
        [event] = await audit_events_for(session, action="delete")
    assert (event.record_type, event.record_id) == ("user_roles", assignment_id)
    assert event.changes == {
        "user_id": {"old": str(user.id), "new": None},
        "role_id": {"old": str(role_id), "new": None},
    }


async def test_grant_and_revoke_delegation_are_create_and_delete(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        owner = await persist_user(uow.session, roles=[OWNER])
        admin = await persist_user(uow.session, roles=[ADMINISTRATOR])
    async with UnitOfWork(sessionmaker, actor_id=owner.id) as uow:
        await service.grant_delegation(
            uow.session,
            actor_id=owner.id,
            user_id=admin.id,
            permission="payroll.finalize",
        )
        # Granting again is a no-op: no second event.
        await service.grant_delegation(
            uow.session,
            actor_id=owner.id,
            user_id=admin.id,
            permission="payroll.finalize",
        )
        delegation_id = await uow.session.scalar(select(UserDelegation.id))
    async with UnitOfWork(sessionmaker, actor_id=owner.id) as uow:
        await service.revoke_delegation(
            uow.session,
            actor_id=owner.id,
            user_id=admin.id,
            permission="payroll.finalize",
        )
        # Revoking a missing delegation is a no-op: no event.
        await service.revoke_delegation(
            uow.session,
            actor_id=owner.id,
            user_id=admin.id,
            permission="payroll.finalize",
        )

    async with sessionmaker() as session:
        events = await audit_events_for(session, record_type="user_delegations")
    assert [(e.action, e.record_id, e.actor_user_id) for e in events] == [
        ("create", delegation_id, owner.id),
        ("delete", delegation_id, owner.id),
    ]
    created, deleted = events
    assert created.changes is not None and deleted.changes is not None
    assert created.changes["user_id"] == {"old": None, "new": str(admin.id)}
    assert created.changes["granted_by_user_id"] == {
        "old": None,
        "new": str(owner.id),
    }
    assert created.changes["permission_delegable"] == {"old": None, "new": True}
    assert deleted.changes["user_id"] == {"old": str(admin.id), "new": None}
