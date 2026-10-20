"""Identity factories (M2-02): users with roles and delegations.

Every factory user has the same password, :data:`TEST_USER_PASSWORD` (test-only,
synthetic). Its Argon2id hash is computed once per test session
(:func:`factory_password_hash`), so creating users does not pay the 19 MiB hashing
cost each time.

Roles and delegations refer to the seeded ``roles`` and ``permissions`` rows:
apply the identity reference seeds before :func:`persist_user`.

:data:`DEMO_ACCOUNTS` lists the local-only demo accounts, one per role
(ADR-0007 D12, #195), and :class:`DemoAccountGenerator` creates one of them
(``identity.demo_owner`` … ``identity.demo_guardian``). The demo Owner
(:data:`DEMO_OWNER_USERNAME` / :data:`DEMO_OWNER_PASSWORD`, #23) is also the
E2E sign-in fixture.
"""

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import cache
from typing import Any

import factory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.identity import service as identity_service
from app.modules.identity.models import (
    Permission,
    Role,
    User,
    UserDelegation,
    UserRole,
)
from app.modules.identity.passwords import hash_password
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ACTIVE,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
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
    privacy_notice_accepted: bool = True,
    **overrides: Any,
) -> User:
    """Persist a :class:`UserFactory` user with ``roles`` and ``delegations``.

    Role and permission codes are resolved against the seeded tables. A
    delegation is recorded as granted by ``granted_by`` (default: the user
    itself, for tests that do not care who granted it). Flushes, never commits.

    The user accepts the current privacy-notice version (#174 E3, through
    ``identity.service.accept_privacy_notice``; one audited acceptance row),
    so the server-side notice gate never gets in the way of tests about
    something else. ``privacy_notice_accepted=False`` gives a user who still
    has to accept it.
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
    if privacy_notice_accepted:
        current = await identity_service.current_privacy_notice(session)
        await identity_service.accept_privacy_notice(
            session, user_id=user.id, version=current.version
        )
    return user


# --- The demo accounts (ADR-0007 D12, amended by #195) -------------------------------

# Local development placeholders, never real credentials: the demo accounts
# exist only in local stacks (APP_ENV=local), including the E2E stack.
# Documented in the README ("Seed data") and in _docs/testing-guidelines.md
# ("E2E data rules"); the E2E tests read the demo Owner's values from
# e2e/tests/support/credentials.ts.
DEMO_OWNER_USERNAME = "demo.owner"
DEMO_OWNER_DISPLAY_NAME = "Demo Owner"
DEMO_OWNER_PASSWORD = "classora-demo-owner"
DEMO_STUDENT_USERNAME = "demo.student"
DEMO_GUARDIAN_USERNAME = "demo.guardian"
DEMO_ACCOUNT_APP_ENVS = frozenset({"local"})
DEMO_ACCOUNT_LANGUAGE = "es"
DEMO_ACCOUNT_THEME = "system"


@dataclass(frozen=True)
class DemoAccount:
    """One local demo account: its sign-in values and its single role code."""

    username: str
    display_name: str
    password: str
    role: str


DEMO_ACCOUNTS: tuple[DemoAccount, ...] = (
    DemoAccount(
        DEMO_OWNER_USERNAME, DEMO_OWNER_DISPLAY_NAME, DEMO_OWNER_PASSWORD, OWNER
    ),
    DemoAccount(
        "demo.admin", "Demo Administrator", "classora-demo-admin", ADMINISTRATOR
    ),
    DemoAccount(
        "demo.coordinator",
        "Demo Coordinator",
        "classora-demo-coordinator",
        ACADEMIC_COORDINATOR,
    ),
    DemoAccount("demo.finance", "Demo Finance", "classora-demo-finance", FINANCE_STAFF),
    DemoAccount("demo.tutor", "Demo Tutor", "classora-demo-tutor", TUTOR),
    DemoAccount(
        DEMO_STUDENT_USERNAME, "Demo Student", "classora-demo-student", STUDENT
    ),
    DemoAccount(
        DEMO_GUARDIAN_USERNAME, "Demo Guardian", "classora-demo-guardian", GUARDIAN
    ),
)
"""Every demo account, in ``make seed-demo`` order (#195 A1)."""

DEMO_OWNER = DEMO_ACCOUNTS[0]


def _settings_app_env() -> str:
    return get_settings().app_env


class DemoAccountGenerator:
    """``identity.<username with . as _>``: one ``active`` demo account for
    local sign-in, with exactly its one role.

    Also stores its preferences (#36 E3): :data:`DEMO_ACCOUNT_LANGUAGE` and
    :data:`DEMO_ACCOUNT_THEME`. Writes nothing outside ``APP_ENV=local``
    (skipped), and nothing when the username already exists, in any state (0
    rows; nothing is repaired), so a second ``make seed-demo`` passes. The
    password is hashed with ``hash_password`` (the current parameters). No
    privacy-notice acceptance is stored.

    The student account is created ``active`` directly, without the identity
    lifecycle services (the demo command registers no student-login gate);
    ``families.demo_families`` creates and checks its login basis.
    """

    def __init__(
        self,
        account: DemoAccount = DEMO_OWNER,
        app_env: Callable[[], str] = _settings_app_env,
    ) -> None:
        self.account = account
        self.name = "identity." + account.username.replace(".", "_")
        self._app_env = app_env

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        app_env = self._app_env()
        if app_env not in DEMO_ACCOUNT_APP_ENVS:
            return GeneratorSkipped(f"APP_ENV={app_env}; local only")
        account = self.account
        existing = await session.scalar(
            select(User.id).where(User.username == account.username)
        )
        if existing is not None:
            return 0
        role_id = (
            await session.execute(select(Role.id).where(Role.code == account.role))
        ).scalar_one()
        user = User(
            username=account.username,
            display_name=account.display_name,
            password_hash=hash_password(account.password),
            state=ACTIVE,
        )
        session.add(user)
        await session.flush()
        session.add(UserRole(user_id=user.id, role_id=role_id))
        await session.flush()
        # Stored preferences (#36 E3), so a demo sign-in looks the same on
        # every machine and the shared E2E account never takes a spec's
        # local language or theme at sign-in.
        await identity_service.update_user_preferences(
            session,
            user.id,
            language=DEMO_ACCOUNT_LANGUAGE,
            theme=DEMO_ACCOUNT_THEME,
        )
        return 1


class DemoOwnerGenerator(DemoAccountGenerator):
    """``identity.demo_owner`` (#23): the :class:`DemoAccountGenerator` of the
    demo Owner, also the E2E sign-in fixture."""

    def __init__(self, app_env: Callable[[], str] = _settings_app_env) -> None:
        super().__init__(DEMO_OWNER, app_env)


DEMO_ACCOUNT_GENERATORS: tuple[DemoAccountGenerator, ...] = (
    DemoOwnerGenerator(),
    *(DemoAccountGenerator(account) for account in DEMO_ACCOUNTS[1:]),
)
"""One generator per :data:`DEMO_ACCOUNTS` entry, in the same order."""
