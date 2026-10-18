"""The identity module's public API (ADR-0002): other modules import only this.

- Usernames: :func:`normalize_username` (ADR-0007, D7).
- Passwords: :func:`hash_password`, :func:`verify_password`,
  :func:`needs_rehash`, :func:`validate_password` (D5, D6; ``passwords.py``).
- Permissions: :func:`effective_permissions` (database) and
  :func:`resolve_permissions` (pure), :func:`may_assign_role`, the role codes
  and :data:`STAFF_ROLES` (D4; ``permissions.py``).
- Delegation: :func:`grant_delegation` and :func:`revoke_delegation`.
- Authorization: :func:`load_principal` (the :class:`~app.core.authz.Principal`
  of an active user; #23 calls it after resolving a session).

Every function runs in the caller's Unit of Work and never commits.
"""

import re
import uuid
from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.identity.errors import (
    DelegationForbiddenError,
    DelegationTargetInvalidError,
    PasswordEqualsUsernameError,
    PasswordPolicyError,
    PasswordTooLongError,
    PasswordTooShortError,
    PermissionNotDelegableError,
    UsernameInvalidError,
)
from app.modules.identity.models import (
    Permission,
    Role,
    RolePermission,
    User,
    UserDelegation,
    UserRole,
)
from app.modules.identity.passwords import (
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)
from app.modules.identity.permissions import (
    ACTIVE,
    ADMINISTRATOR,
    ARCHIVED,
    DELEGABLE_PERMISSIONS,
    OWNER,
    ROLE_CODES,
    STAFF_ROLES,
    USER_DELEGATION_MANAGE,
    may_assign_role,
    resolve_permissions,
)

__all__ = [
    "ACTIVE",
    "ADMINISTRATOR",
    "OWNER",
    "ROLE_CODES",
    "STAFF_ROLES",
    "DelegationForbiddenError",
    "DelegationTargetInvalidError",
    "PasswordEqualsUsernameError",
    "PasswordPolicyError",
    "PasswordTooLongError",
    "PasswordTooShortError",
    "PermissionNotDelegableError",
    "UsernameInvalidError",
    "effective_permissions",
    "grant_delegation",
    "hash_password",
    "load_principal",
    "may_assign_role",
    "needs_rehash",
    "normalize_username",
    "resolve_permissions",
    "revoke_delegation",
    "validate_password",
    "verify_password",
]

_USERNAME = re.compile(r"[a-z0-9._-]{3,64}")


def normalize_username(raw: str) -> str:
    """The stored form of a username, or :class:`UsernameInvalidError`.

    Trims surrounding whitespace, rejects any non-ASCII character *before*
    lower-casing (so the Kelvin sign cannot become ``k``), lower-cases, then
    requires 3 to 64 characters from ``a-z 0-9 . _ -``.
    """
    trimmed = raw.strip()
    if not trimmed.isascii():
        raise UsernameInvalidError()
    username = trimmed.lower()
    if _USERNAME.fullmatch(username) is None:
        raise UsernameInvalidError()
    return username


async def _user_state(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    state: str | None = await session.scalar(
        select(User.state).where(User.id == user_id)
    )
    return state


async def _user_roles(session: AsyncSession, user_id: uuid.UUID) -> set[str]:
    rows = await session.scalars(
        select(Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    )
    return set(rows)


async def effective_permissions(
    session: AsyncSession, user_id: uuid.UUID
) -> frozenset[str]:
    """The user's effective permission codes, from the database (ADR-0007, D4).

    Loads the state, the roles, the seeded ``role_permissions`` of those roles
    and the delegations, then applies :func:`resolve_permissions`. An unknown
    user, or a user who is not ``active``, has none.
    """
    state = await _user_state(session, user_id)
    if state is None:
        return frozenset()
    roles = await _user_roles(session, user_id)
    role_permissions: defaultdict[str, set[str]] = defaultdict(set)
    rows = await session.execute(
        select(Role.code, Permission.code)
        .select_from(UserRole)
        .join(Role, Role.id == UserRole.role_id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(UserRole.user_id == user_id)
    )
    for role_code, permission_code in rows:
        role_permissions[role_code].add(permission_code)
    delegated = await session.scalars(
        select(Permission.code)
        .join(UserDelegation, UserDelegation.permission_id == Permission.id)
        .where(UserDelegation.user_id == user_id)
    )
    return resolve_permissions(
        state, roles, set(delegated), role_permissions=role_permissions
    )


async def load_principal(session: AsyncSession, user_id: uuid.UUID) -> Principal | None:
    """The :class:`Principal` of an ``active`` user, or ``None``.

    ``None`` for an unknown user or one whose state is not ``active``.
    Otherwise the user's role codes and :func:`effective_permissions`.
    Never commits.
    """
    if await _user_state(session, user_id) != ACTIVE:
        return None
    return Principal(
        user_id=user_id,
        roles=frozenset(await _user_roles(session, user_id)),
        permissions=await effective_permissions(session, user_id),
    )


async def _check_delegation(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    user_id: uuid.UUID,
    permission: str,
) -> uuid.UUID:
    """Apply the delegation rules; return the permission's id."""
    if USER_DELEGATION_MANAGE not in await effective_permissions(session, actor_id):
        raise DelegationForbiddenError()
    if permission not in DELEGABLE_PERMISSIONS:
        raise PermissionNotDelegableError()
    state = await _user_state(session, user_id)
    if (
        state is None
        or state == ARCHIVED
        or ADMINISTRATOR not in await _user_roles(session, user_id)
    ):
        raise DelegationTargetInvalidError()
    permission_id = await session.scalar(
        select(Permission.id).where(Permission.code == permission)
    )
    if permission_id is None:
        # The catalog lists it, but the reference seeds have not run.
        raise RuntimeError("Permissions are not seeded: run `make seed` first.")
    return permission_id


async def grant_delegation(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    user_id: uuid.UUID,
    permission: str,
) -> None:
    """Delegate ``permission`` to the Administrator ``user_id`` (idempotent).

    Only an active Owner (``user.delegation.manage``) may grant, only a
    delegable permission, and only to a non-archived Administrator; otherwise
    a typed error is raised and nothing changes. Granting an existing
    delegation is a no-op. Callers add reauthentication and audit (#31).
    """
    permission_id = await _check_delegation(
        session, actor_id=actor_id, user_id=user_id, permission=permission
    )
    await session.execute(
        insert(UserDelegation)
        .values(
            user_id=user_id,
            permission_id=permission_id,
            granted_by_user_id=actor_id,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "permission_id"])
    )


async def revoke_delegation(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    user_id: uuid.UUID,
    permission: str,
) -> None:
    """Remove a delegation (same rules as :func:`grant_delegation`).

    Revoking a delegation that does not exist is a no-op.
    """
    permission_id = await _check_delegation(
        session, actor_id=actor_id, user_id=user_id, permission=permission
    )
    await session.execute(
        delete(UserDelegation).where(
            UserDelegation.user_id == user_id,
            UserDelegation.permission_id == permission_id,
        )
    )
