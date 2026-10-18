"""Identity factories (M2-02): users with roles and delegations.

Every factory user has the same password, :data:`TEST_USER_PASSWORD` (test-only,
synthetic). Its Argon2id hash is computed once per test session
(:func:`factory_password_hash`), so creating users does not pay the 19 MiB hashing
cost each time.

Roles and delegations refer to the seeded ``roles`` and ``permissions`` rows:
apply the identity reference seeds before :func:`persist_user`.
"""

import uuid
from collections.abc import Iterable
from functools import cache
from typing import Any

import factory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import (
    Permission,
    Role,
    User,
    UserDelegation,
    UserRole,
)
from app.modules.identity.passwords import hash_password
from tests.factories.base import ClassoraFactory, persist

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
