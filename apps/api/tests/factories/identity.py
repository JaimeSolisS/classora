"""Identity factories (M2-02): users with roles and delegations.

Every factory user has the same password, :data:`TEST_USER_PASSWORD` (test-only,
synthetic). Its Argon2id hash is computed once per test session
(:func:`factory_password_hash`), so creating users does not pay the 19 MiB hashing
cost each time.

Roles and delegations refer to the seeded ``roles`` and ``permissions`` rows:
apply the identity reference seeds before :func:`persist_user`.

:class:`DemoOwnerGenerator` is the ``identity.demo_owner`` demo generator
(ADR-0007 D12): the local-only demo Owner :data:`DEMO_OWNER_USERNAME` /
:data:`DEMO_OWNER_PASSWORD`, also the E2E sign-in fixture.
"""

import uuid
from collections.abc import Callable, Iterable
from functools import cache
from typing import Any

import factory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.identity.models import (
    Permission,
    Role,
    User,
    UserDelegation,
    UserRole,
)
from app.modules.identity.passwords import hash_password
from app.modules.identity.permissions import ACTIVE, OWNER
from tests.factories.base import ClassoraFactory, GeneratorSkipped, persist

TEST_USER_PASSWORD = "factory-test-password"
"""The password of every factory user (test-only; never a real credential)."""


@cache
def factory_password_hash() -> str:
    """The Argon2id hash of :data:`TEST_USER_PASSWORD` (computed once)."""
    return hash_password(TEST_USER_PASSWORD)


class UserFactory(ClassoraFactory):
    """An ``active`` user with a synthetic, valid, unique username."""

    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user{n:04d}")
    display_name = factory.Faker("name")
    state = "active"
    password_hash = factory.LazyFunction(factory_password_hash)


async def persist_user(
    session: AsyncSession,
    *,
    roles: Iterable[str] = (),
    delegations: Iterable[str] = (),
    granted_by: uuid.UUID | None = None,
    **overrides: Any,
) -> User:
    """Persist a :class:`UserFactory` user with ``roles`` and ``delegations``.

    Role and permission codes are resolved against the seeded tables. A
    delegation is recorded as granted by ``granted_by`` (default: the user
    itself, for tests that do not care who granted it). Flushes, never commits.
    """
    user: User = await persist(session, UserFactory, **overrides)
    for code in roles:
        role_id = (
            await session.execute(select(Role.id).where(Role.code == code))
        ).scalar_one()
        session.add(UserRole(user_id=user.id, role_id=role_id))
    for code in delegations:
        permission_id = (
            await session.execute(select(Permission.id).where(Permission.code == code))
        ).scalar_one()
        session.add(
            UserDelegation(
                user_id=user.id,
                permission_id=permission_id,
                granted_by_user_id=granted_by or user.id,
            )
        )
    await session.flush()
    return user


# --- The demo Owner (ADR-0007 D12) ---------------------------------------------------

# Local development placeholders, never real credentials: the demo Owner exists
# only in local stacks (APP_ENV=local), including the E2E stack. Documented in
# the README and in _docs/testing-guidelines.md ("E2E data rules"); the E2E
# tests read the same values from e2e/tests/support/credentials.ts.
DEMO_OWNER_USERNAME = "demo.owner"
DEMO_OWNER_DISPLAY_NAME = "Demo Owner"
DEMO_OWNER_PASSWORD = "classora-demo-owner"
DEMO_OWNER_APP_ENVS = frozenset({"local"})


def _settings_app_env() -> str:
    return get_settings().app_env


class DemoOwnerGenerator:
    """``identity.demo_owner``: one ``active`` Owner for local sign-in.

    Writes nothing outside ``APP_ENV=local`` (skipped), and nothing when the
    user already exists (0 rows), so a second ``make seed-demo`` passes.
    The password is hashed with ``hash_password`` (the current parameters).
    """

    name = "identity.demo_owner"

    def __init__(self, app_env: Callable[[], str] = _settings_app_env) -> None:
        self._app_env = app_env

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        app_env = self._app_env()
        if app_env not in DEMO_OWNER_APP_ENVS:
            return GeneratorSkipped(f"APP_ENV={app_env}; local only")
        existing = await session.scalar(
            select(User.id).where(User.username == DEMO_OWNER_USERNAME)
        )
        if existing is not None:
            return 0
        owner_role_id = (
            await session.execute(select(Role.id).where(Role.code == OWNER))
        ).scalar_one()
        user = User(
            username=DEMO_OWNER_USERNAME,
            display_name=DEMO_OWNER_DISPLAY_NAME,
            password_hash=hash_password(DEMO_OWNER_PASSWORD),
            state=ACTIVE,
        )
        session.add(user)
        await session.flush()
        session.add(UserRole(user_id=user.id, role_id=owner_role_id))
        await session.flush()
        return 1
