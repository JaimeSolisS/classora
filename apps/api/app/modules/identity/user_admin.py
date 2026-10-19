"""User administration (#31; spec §2, §16; ADR-0007 D4, D7, D8, D10).

Owners and Administrators (``user.view``) list and open any user; with
``user.manage`` they edit the display name and the optional contact fields
(``email``, ``phone``; never used to sign in) and suspend, archive or
reactivate accounts.

**Who may manage whom** (ADR-0007 D4 as amended by #31):
:func:`~app.modules.identity.permissions.may_manage_user`. A caller manages a
user only when they may assign **every** role that user holds; their own
profile is always editable, but nobody suspends, archives or reactivates
themselves (``403 permission_denied``).

**State changes** go through :func:`service.change_user_state`, which
revokes the sessions (#25) and holds the last-active-Owner guard (``409
last_active_owner``). This module adds the per-route transitions
(:data:`STATE_ACTIONS`): any other case, including the same state, is ``409
user_state_transition_invalid``.

**Versions:** every change sends the ``version`` the caller loaded; a stale
one is ``409 version_conflict`` and changes nothing.

**Roles and delegations (#170):** :func:`update_roles` and
:func:`update_delegations` take the complete new set and apply the
difference through the identity service (``add_user_role`` /
``remove_user_role``, ``grant_delegation`` / ``revoke_delegation``). A change
first increments ``users.version`` (the row is touched and flushed, which
also locks it, so of two concurrent changes from one version the second gets
``409 version_conflict``). The same set is a no-op. Role changes revoke the
user's sessions (#25), delegation changes don't.

**Audit:** automatic only (``users`` ``update`` for the changed fields and
the state, ``user_roles`` / ``user_delegations`` ``create`` / ``delete``,
plus ``identity.sessions_revoked`` from #25). No named event and no
``details`` key of its own; a version bump alone writes no ``users`` event.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import asdict, dataclass
from typing import Final, Literal

from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.core.models import ensure_version
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.identity import service
from app.modules.identity.errors import (
    DelegationTargetInvalidError,
    InvalidUserStateTransitionError,
)
from app.modules.identity.models import (
    Permission,
    Role,
    User,
    UserDelegation,
    UserRole,
)
from app.modules.identity.permissions import (
    ACTIVE,
    ADMINISTRATOR,
    ARCHIVED,
    PENDING,
    ROLE_CODES,
    SUSPENDED,
    allowed_user_actions,
    may_assign_role,
    may_manage_user,
)
from app.modules.identity.schemas import (
    UserAllowedActions,
    UserDetail,
    UserListItem,
    UserUpdate,
)

StateAction = Literal["suspend", "archive", "reactivate"]


@dataclass(frozen=True)
class StateActionRule:
    """One state route: the target state and the states it may start from."""

    new_state: str
    from_states: frozenset[str]
    pending_without_password: bool = False
    """Also from ``pending`` when the user has no password (an open
    invitation; a pending user with a password is a registration request,
    decided on its own page, #169)."""


STATE_ACTIONS: Final[dict[StateAction, StateActionRule]] = {
    "suspend": StateActionRule(SUSPENDED, frozenset({ACTIVE})),
    "archive": StateActionRule(
        ARCHIVED, frozenset({ACTIVE, SUSPENDED}), pending_without_password=True
    ),
    # pending -> active is activation (#29, #169), not reactivation.
    "reactivate": StateActionRule(ACTIVE, frozenset({SUSPENDED, ARCHIVED})),
}

_ROLE_ORDER: Final = {code: index for index, code in enumerate(ROLE_CODES)}


def _sorted_roles(roles: Collection[str]) -> list[str]:
    """Role codes in the catalog order (``ROLE_CODES``)."""
    return sorted(roles, key=_ROLE_ORDER.__getitem__)


_LIKE_ESCAPE: Final = "/"


def _escape_like(text: str) -> str:
    """``text`` for a ``LIKE … ESCAPE '/'`` pattern: ``%``, ``_`` and ``/``
    match literally (``\\`` is not special with this escape character)."""
    return (
        text.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )


async def list_users(
    session: AsyncSession,
    *,
    q: str | None,
    role: str | None,
    state: str | None,
    page: OffsetParams,
) -> OffsetPage[UserListItem]:
    """The users matching every filter, by ``lower(display_name)``, then
    ``username``, then ``id``.

    ``q`` is a case-insensitive substring of the username or the display name
    (``%``, ``_`` and ``\\`` match literally; accents are not folded). At most
    three statements: the page, the total and the roles of the page.
    """
    conditions: list[ColumnElement[bool]] = []
    if q:
        pattern = f"%{_escape_like(q.lower())}%"
        conditions.append(
            func.lower(User.username).like(pattern, escape=_LIKE_ESCAPE)
            | func.lower(User.display_name).like(pattern, escape=_LIKE_ESCAPE)
        )
    if state is not None:
        conditions.append(User.state == state)
    if role is not None:
        conditions.append(
            exists()
            .where(UserRole.user_id == User.id)
            .where(UserRole.role_id == Role.id)
            .where(Role.code == role)
        )
    users = list(
        await session.scalars(
            select(User)
            .where(*conditions)
            .order_by(func.lower(User.display_name), User.username, User.id)
            .limit(page.limit)
            .offset(page.offset)
        )
    )
    total = await session.scalar(select(func.count(User.id)).where(*conditions))
    roles = await _roles_by_user(session, [user.id for user in users])
    return OffsetPage[UserListItem](
        items=[_list_item(user, roles[user.id]) for user in users],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


async def _roles_by_user(
    session: AsyncSession, user_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, set[str]]:
    """The role codes of each user, in one statement (none when empty)."""
    roles: defaultdict[uuid.UUID, set[str]] = defaultdict(set)
    if user_ids:
        rows = await session.execute(
            select(UserRole.user_id, Role.code)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id.in_(user_ids))
        )
        for user_id, code in rows:
            roles[user_id].add(code)
    return roles


def _list_item(user: User, roles: Collection[str]) -> UserListItem:
    return UserListItem.model_validate(
        {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "state": user.state,
            "roles": _sorted_roles(roles),
            "created_at": user.created_at,
        }
    )


async def _user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise RecordNotFoundError()
    return user


async def _roles_of(session: AsyncSession, user_id: uuid.UUID) -> set[str]:
    return (await _roles_by_user(session, [user_id]))[user_id]


async def _detail(
    session: AsyncSession, principal: Principal, user: User, roles: set[str]
) -> UserDetail:
    delegations = await session.scalars(
        select(Permission.code)
        .join(UserDelegation, UserDelegation.permission_id == Permission.id)
        .where(UserDelegation.user_id == user.id)
        .order_by(Permission.code)
    )
    password_set = user.password_hash is not None
    actions = allowed_user_actions(
        actor_id=principal.user_id,
        actor_roles=principal.roles,
        actor_permissions=principal.permissions,
        target_id=user.id,
        target_state=user.state,
        target_roles=roles,
        target_password_set=password_set,
    )
    return UserDetail.model_validate(
        {
            **_list_item(user, roles).model_dump(),
            "email": user.email,
            "phone": user.phone,
            "version": user.version,
            "updated_at": user.updated_at,
            "delegations": list(delegations),
            "password_set": password_set,
            "allowed_actions": UserAllowedActions.model_validate(asdict(actions)),
        }
    )


async def get_user_detail(
    session: AsyncSession, *, principal: Principal, user_id: uuid.UUID
) -> UserDetail:
    """One user's account (``404 not_found`` when unknown), with the caller's
    ``allowed_actions``."""
    user = await _user(session, user_id)
    return await _detail(session, principal, user, await _roles_of(session, user_id))


async def _managed_user(
    session: AsyncSession, principal: Principal, user_id: uuid.UUID
) -> tuple[User, set[str]]:
    """The user and their roles: ``404`` when unknown, ``403`` unless the
    caller may manage them (:func:`may_manage_user`)."""
    user = await _user(session, user_id)
    roles = await _roles_of(session, user_id)
    if not may_manage_user(
        principal.permissions,
        actor_id=principal.user_id,
        target_id=user.id,
        target_roles=roles,
    ):
        raise PermissionDeniedError()
    return user, roles


async def update_user_profile(
    session: AsyncSession,
    *,
    principal: Principal,
    user_id: uuid.UUID,
    body: UserUpdate,
) -> UserDetail:
    """Change the display name and contact fields that were sent.

    Order: ``404`` → ``403`` (:func:`may_manage_user`) → ``409
    version_conflict`` → the update. A body that changes nothing changes no
    version and writes no event. Allowed in every state.
    """
    user, roles = await _managed_user(session, principal, user_id)
    ensure_version(user, body.version)
    sent = body.model_fields_set
    changes: dict[str, str | None] = {}
    if "display_name" in sent and body.display_name != user.display_name:
        changes["display_name"] = body.display_name
    if "email" in sent and body.email != user.email:
        changes["email"] = body.email
    if "phone" in sent and body.phone != user.phone:
        changes["phone"] = body.phone
    if changes:
        for field, value in changes.items():
            setattr(user, field, value)
        await session.flush()
    return await _detail(session, principal, user, roles)


async def change_state(
    session: AsyncSession,
    *,
    principal: Principal,
    user_id: uuid.UUID,
    action: StateAction,
    version: int,
) -> UserDetail:
    """Suspend, archive or reactivate a user (:data:`STATE_ACTIONS`).

    Order (after the route's permission and reauthentication checks): ``404``
    → ``403`` (:func:`may_manage_user`, then the caller's own account) →
    ``409 version_conflict`` → ``409 user_state_transition_invalid`` → the
    change through :func:`service.change_user_state` (``409
    last_active_owner``; suspending and archiving revoke every session).
    """
    user, roles = await _managed_user(session, principal, user_id)
    if user.id == principal.user_id:
        raise PermissionDeniedError()
    ensure_version(user, version)
    rule = STATE_ACTIONS[action]
    allowed = user.state in rule.from_states or (
        rule.pending_without_password
        and user.state == PENDING
        and user.password_hash is None
    )
    if not allowed:
        raise InvalidUserStateTransitionError()
    await service.change_user_state(session, user_id=user.id, new_state=rule.new_state)
    return await _detail(session, principal, user, roles)


async def _delegations_of(session: AsyncSession, user_id: uuid.UUID) -> set[str]:
    rows = await session.scalars(
        select(Permission.code)
        .join(UserDelegation, UserDelegation.permission_id == Permission.id)
        .where(UserDelegation.user_id == user_id)
    )
    return set(rows)


async def _bump_version(session: AsyncSession, user: User) -> None:
    """Increment ``users.version`` now: touching ``updated_at`` makes the ORM
    run its versioned ``UPDATE`` (which locks the row; a concurrent change
    from the same version then fails with ``version_conflict``). Only
    ``version``/``updated_at`` change, which the audit leaves out (#22)."""
    user.updated_at = func.now()
    await session.flush()
    await session.refresh(user, ["updated_at"])


async def update_roles(
    session: AsyncSession,
    *,
    principal: Principal,
    user_id: uuid.UUID,
    roles: Collection[str],
    version: int,
) -> UserDetail:
    """Make ``roles`` the user's complete set of roles (#170 A).

    Order (after the route's permission and reauthentication checks and the
    body's ``422``): ``404`` → ``403`` (:func:`may_manage_user`) → ``403``
    unless :func:`may_assign_role` holds for **every** role added or removed
    (ADR-0007 D4: removing needs the same authority as assigning) → ``409
    version_conflict`` → the version bump → added roles through
    :func:`service.add_user_role`, then removed roles through
    :func:`service.remove_user_role`, each in catalog order. Removing
    ``owner`` from the only active Owner raises ``409 last_active_owner`` and
    the caller's Unit of Work rolls everything back. Allowed in every state;
    the caller's own roles too (their sessions then end, including this one).
    """
    user, current = await _managed_user(session, principal, user_id)
    wanted = set(roles)
    added = wanted - current
    removed = current - wanted
    if not all(
        may_assign_role(principal.permissions, role) for role in added | removed
    ):
        raise PermissionDeniedError()
    ensure_version(user, version)
    if added or removed:
        await _bump_version(session, user)
        for role in _sorted_roles(added):
            await service.add_user_role(session, user_id=user.id, role=role)
        for role in _sorted_roles(removed):
            await service.remove_user_role(session, user_id=user.id, role=role)
    return await _detail(session, principal, user, wanted)


async def update_delegations(
    session: AsyncSession,
    *,
    principal: Principal,
    user_id: uuid.UUID,
    permissions: Collection[str],
    version: int,
) -> UserDetail:
    """Make ``permissions`` the user's complete set of delegations (#170 C).

    Order (after the route's ``user.delegation.manage`` and reauthentication
    checks and the body's ``422``): ``404`` → ``409 version_conflict`` →
    ``422 delegation_target_invalid`` when a code is **added** and the user
    doesn't hold ``administrator`` or is ``archived`` → the version bump →
    :func:`service.grant_delegation` for the added codes, then
    :func:`service.revoke_delegation` for the removed ones, sorted, with the
    caller as the actor (they re-check ``user.delegation.manage``). Removing
    only is allowed for any user (#170 B2). No session is revoked: the
    Administrator's next request sees the change (#25 F5).
    """
    user = await _user(session, user_id)
    ensure_version(user, version)
    roles = await _roles_of(session, user.id)
    current = await _delegations_of(session, user.id)
    wanted = set(permissions)
    added = wanted - current
    removed = current - wanted
    if added and (user.state == ARCHIVED or ADMINISTRATOR not in roles):
        raise DelegationTargetInvalidError()
    if added or removed:
        await _bump_version(session, user)
        for permission in sorted(added):
            await service.grant_delegation(
                session,
                actor_id=principal.user_id,
                user_id=user.id,
                permission=permission,
            )
        for permission in sorted(removed):
            await service.revoke_delegation(
                session,
                actor_id=principal.user_id,
                user_id=user.id,
                permission=permission,
            )
    return await _detail(session, principal, user, roles)
