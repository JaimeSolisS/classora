"""Role members and account contact edits for other modules (#43 C1, C3).

Other modules read and change accounts only through ``identity.service``
(ADR-0002). The tutor profiles of ``staff`` (#43) use:

- :func:`list_role_members` and :func:`get_role_member`: the users holding one
  role, **with** their contact fields (``email``, ``phone``), as
  :class:`~app.modules.identity.schemas.RoleMember`. The same filters and
  order as the user list (#31; :func:`user_conditions`).
- :func:`update_account_contact`: change the display name and the contact
  fields of one account. No authority check: the caller has checked it.
  ``users`` is audited and versioned, so a change writes its ``users``
  ``update`` event and increments ``users.version``.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections import defaultdict
from collections.abc import Collection, Sequence
from typing import Final

from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.identity.models import Role, User, UserRole
from app.modules.identity.preferences import UNSET, Unset
from app.modules.identity.schemas import RoleMember

LIKE_ESCAPE: Final = "/"


def escape_like(text: str) -> str:
    """``text`` for a ``LIKE … ESCAPE '/'`` pattern: ``%``, ``_`` and ``/``
    match literally (``\\`` is not special with this escape character)."""
    return (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


def user_conditions(
    *, q: str | None, role: str | None, state: str | None
) -> list[ColumnElement[bool]]:
    """The ``WHERE`` conditions of the user list (#31): ``q`` is a
    case-insensitive substring of the username or the display name (accents
    are not folded), ``role`` a role the user holds, ``state`` their state."""
    conditions: list[ColumnElement[bool]] = []
    if q:
        pattern = f"%{escape_like(q.lower())}%"
        conditions.append(
            func.lower(User.username).like(pattern, escape=LIKE_ESCAPE)
            | func.lower(User.display_name).like(pattern, escape=LIKE_ESCAPE)
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
    return conditions


USER_ORDER: Final = (func.lower(User.display_name), User.username, User.id)
"""The user list order: display name ignoring case, then username, then id."""


async def roles_by_user(
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


def _member(user: User, roles: Collection[str]) -> RoleMember:
    return RoleMember.model_validate(
        {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "state": user.state,
            "email": user.email,
            "phone": user.phone,
            "roles": frozenset(roles),
        }
    )


async def list_role_members(
    session: AsyncSession,
    role: str,
    *,
    q: str | None,
    state: str | None,
    page: OffsetParams,
    user_ids: Collection[uuid.UUID] | None = None,
) -> OffsetPage[RoleMember]:
    """The users holding ``role`` and matching ``q`` and ``state`` (as the
    #31 user list), in its order. ``user_ids``, when given, limits the page to
    those users (a record policy). Three statements: the page, the total and
    the roles of the page."""
    conditions = user_conditions(q=q, role=role, state=state)
    if user_ids is not None:
        conditions.append(User.id.in_(list(user_ids)))
    users = list(
        await session.scalars(
            select(User)
            .where(*conditions)
            .order_by(*USER_ORDER)
            .limit(page.limit)
            .offset(page.offset)
        )
    )
    total = await session.scalar(select(func.count(User.id)).where(*conditions))
    roles = await roles_by_user(session, [user.id for user in users])
    return OffsetPage[RoleMember](
        items=[_member(user, roles[user.id]) for user in users],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


async def get_role_member(
    session: AsyncSession, role: str, user_id: uuid.UUID
) -> RoleMember | None:
    """The user ``user_id`` when they hold ``role`` now; ``None`` when they
    are unknown or don't hold it."""
    user = await session.get(User, user_id)
    if user is None:
        return None
    roles = (await roles_by_user(session, [user_id]))[user_id]
    if role not in roles:
        return None
    return _member(user, roles)


async def update_account_contact(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    display_name: str | Unset = UNSET,
    email: str | None | Unset = UNSET,
    phone: str | None | Unset = UNSET,
) -> bool:
    """Set the fields that are not :data:`UNSET` and differ from the stored
    ones; whether anything changed.

    No authority check (the caller checked it) and no version check (the
    caller's own record carries the version the client loaded). The ORM's
    versioned ``UPDATE`` still guards ``users.version``: a concurrent change
    of the same account makes this flush fail with ``version_conflict``.
    ``404 not_found`` for an unknown user.
    """
    user = await session.get(User, user_id)
    if user is None:
        raise RecordNotFoundError()
    changed = False
    if not isinstance(display_name, Unset) and display_name != user.display_name:
        user.display_name = display_name
        changed = True
    if not isinstance(email, Unset) and email != user.email:
        user.email = email
        changed = True
    if not isinstance(phone, Unset) and phone != user.phone:
        user.phone = phone
        changed = True
    if changed:
        await session.flush()
    return changed
