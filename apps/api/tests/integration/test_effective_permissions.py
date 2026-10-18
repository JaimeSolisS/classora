"""The effective-permissions loader and the factories (M2-02 G2, I1)."""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.identity import service
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    PERMISSION_CODES,
    ROLE_CODES,
    STUDENT,
    TUTOR,
)
from tests.factories.identity import (
    TEST_USER_PASSWORD,
    factory_password_hash,
    persist_user,
)
from tests.integration.conftest import run_sql_async, short_lived_engine

pytestmark = pytest.mark.anyio


@pytest.fixture
async def session(seeded_url: str) -> AsyncIterator[AsyncSession]:
    """A session on a seeded database; everything is rolled back afterwards."""
    engine = short_lived_engine(seeded_url)
    try:
        async with create_sessionmaker(engine)() as db_session:
            yield db_session
            await db_session.rollback()
    finally:
        await engine.dispose()


CASES = [("active", [role], []) for role in ROLE_CODES] + [
    ("active", [TUTOR, GUARDIAN], []),
    ("active", [FINANCE_STAFF, ACADEMIC_COORDINATOR], []),
    ("active", [ADMINISTRATOR], ["payroll.finalize", "user.admin_role.assign"]),
    ("active", [TUTOR], ["payroll.finalize"]),  # no longer an Administrator
    ("active", [], []),
    ("pending", [OWNER], []),
    ("suspended", [ADMINISTRATOR], ["schedule.override"]),
    ("archived", [STUDENT], []),
]


@pytest.mark.parametrize(("state", "roles", "delegations"), CASES)
async def test_database_result_equals_the_pure_resolution(
    session, state, roles, delegations
):
    user = await persist_user(
        session, roles=roles, delegations=delegations, state=state
    )

    loaded = await service.effective_permissions(session, user.id)

    assert loaded == service.resolve_permissions(state, roles, delegations)


async def test_owner_has_every_seeded_permission(session):
    owner = await persist_user(session, roles=[OWNER])

    assert await service.effective_permissions(session, owner.id) == PERMISSION_CODES


async def test_unknown_user_has_no_permissions(session):
    assert await service.effective_permissions(session, uuid.uuid7()) == frozenset()


async def test_loader_reads_the_seeded_role_permissions(seeded_url):
    # The loader uses the database mapping, not the code constant: a pair
    # removed from role_permissions (as a reconciling seed would) is gone.
    await run_sql_async(
        seeded_url,
        "DELETE FROM role_permissions WHERE role_id = "
        "(SELECT id FROM roles WHERE code = 'tutor')",
    )
    engine = short_lived_engine(seeded_url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            tutor = await persist_user(uow.session, roles=[TUTOR])
            assert await service.effective_permissions(uow.session, tutor.id) == (
                frozenset()
            )
            guardian = await persist_user(uow.session, roles=[TUTOR, GUARDIAN])
            assert await service.effective_permissions(uow.session, guardian.id) == {
                "student.view",
                "guardian.view",
                "consent.record",
            }
    finally:
        await engine.dispose()


async def test_retired_codes_in_the_database_are_ignored(session):
    # A code removed from the catalog may stay in its table (F3).
    await session.execute(
        text("INSERT INTO permissions (code, delegable) VALUES ('retired.code', false)")
    )
    await session.execute(
        text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT r.id, p.id FROM roles r, permissions p "
            "WHERE r.code = 'tutor' AND p.code = 'retired.code'"
        )
    )
    tutor = await persist_user(session, roles=[TUTOR])

    assert await service.effective_permissions(session, tutor.id) == {"student.view"}


# --- I1: factories -------------------------------------------------------------


async def test_user_factory_builds_valid_users(session):
    users = [await persist_user(session) for _ in range(3)]

    assert len({u.username for u in users}) == 3
    for user in users:
        assert service.normalize_username(user.username) == user.username
        assert user.state == "active"
        assert user.display_name.strip()
        assert user.password_hash == factory_password_hash()
        assert user.id.version == 7
    assert service.verify_password(users[0].password_hash, TEST_USER_PASSWORD)


async def test_persist_user_never_commits(seeded_url):
    engine = short_lived_engine(seeded_url)
    try:
        async with create_sessionmaker(engine)() as db_session:
            await persist_user(
                db_session, roles=[ADMINISTRATOR], delegations=["payroll.finalize"]
            )
            await db_session.rollback()
    finally:
        await engine.dispose()

    rows = await run_sql_async(
        seeded_url,
        "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM user_roles), "
        "(SELECT count(*) FROM user_delegations)",
    )
    assert tuple(rows[0]) == (0, 0, 0)
