"""``identity.service.load_principal`` against a seeded database (M2-03 D)."""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.db import create_sessionmaker
from app.modules.identity import service
from app.modules.identity.permissions import (
    ADMINISTRATOR,
    GUARDIAN,
    PAYROLL_FINALIZE,
    ROLE_CODES,
    ROLE_PERMISSIONS,
    SCHEDULE_OVERRIDE,
    TUTOR,
)
from tests.factories.identity import persist_user
from tests.integration.conftest import short_lived_engine

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


@pytest.mark.parametrize("role", ROLE_CODES)
async def test_one_role_gets_exactly_its_permissions(session, role):
    user = await persist_user(session, roles=[role])

    principal = await service.load_principal(session, user.id)

    assert principal == Principal(
        user_id=user.id,
        roles=frozenset({role}),
        permissions=ROLE_PERMISSIONS[role],
    )


async def test_several_roles_are_a_union(session):
    user = await persist_user(session, roles=[TUTOR, GUARDIAN])

    principal = await service.load_principal(session, user.id)

    assert principal is not None
    assert principal.roles == {TUTOR, GUARDIAN}
    assert principal.permissions == ROLE_PERMISSIONS[TUTOR] | ROLE_PERMISSIONS[GUARDIAN]


async def test_administrator_delegation_is_included(session):
    user = await persist_user(
        session, roles=[ADMINISTRATOR], delegations=[PAYROLL_FINALIZE]
    )

    principal = await service.load_principal(session, user.id)

    assert principal is not None
    assert principal.permissions == ROLE_PERMISSIONS[ADMINISTRATOR] | {PAYROLL_FINALIZE}
    assert SCHEDULE_OVERRIDE not in principal.permissions


@pytest.mark.parametrize("state", ["pending", "suspended", "archived"])
async def test_a_user_who_is_not_active_has_no_principal(session, state):
    user = await persist_user(session, roles=[ADMINISTRATOR], state=state)

    assert await service.load_principal(session, user.id) is None


async def test_an_unknown_user_has_no_principal(session):
    assert await service.load_principal(session, uuid.uuid7()) is None


async def test_load_principal_never_commits(session):
    user = await persist_user(session, roles=[TUTOR])

    await service.load_principal(session, user.id)

    assert session.in_transaction()
    await session.rollback()
    assert await service.load_principal(session, user.id) is None
