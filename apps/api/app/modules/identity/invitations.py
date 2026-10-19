"""Invitations (#29; spec §9; ADR-0007 D4, D7, D10, D11).

An Owner or Administrator invites a person for one role. The invitation
creates a ``pending`` user without a password, gives it the role and issues a
single-use ``invitation`` token (``app.modules.identity.tokens``), shown once
as a link. The person opens the link, sets a password and becomes ``active``.
No email is sent (spec §1, §14).

Authority (ADR-0007 D4): an invitation that carries a role counts as
assigning it, so :func:`~app.modules.identity.permissions.may_assign_role`
decides, before any reauthentication. A staff role (``STAFF_ROLES``) needs a
recent reauthentication (D10; :class:`~app.core.reauth.RecentAuthGuard`).

Audit (#22): ``identity.invitation_issued`` (by the admin) and
``identity.invitation_redeemed`` (no actor), each with the token row's id as
``link_id``. No event holds a password, raw token or token hash. A request
that changes nothing (``409``, ``410``, ``422``, ``429``) writes no event.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable
from typing import Final

from sqlalchemy import and_, exists, func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import record_audit_event
from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.core.pagination import OffsetPage, OffsetParams
from app.core.reauth import RecentAuthGuard
from app.modules.identity import service, tokens
from app.modules.identity.errors import TokenInvalidError, UsernameTakenError
from app.modules.identity.models import AccountToken, Role, User, UserRole
from app.modules.identity.permissions import ACTIVE, PENDING, STAFF_ROLES
from app.modules.identity.schemas import (
    InvitationIssued,
    InvitationListItem,
    InvitationLookup,
)

INVITATION_ISSUED_EVENT: Final = "identity.invitation_issued"
INVITATION_REDEEMED_EVENT: Final = "identity.invitation_redeemed"

INVITE_PATH: Final = "/invite"
"""The web page that redeems an invitation (``#token=`` in the fragment)."""

_USERNAME_UNIQUE: Final = "uq_users_username"


def invitation_url(app_origin: str, raw_token: str) -> str:
    """``{APP_ORIGIN}/invite#token=<token>``. The token travels in the
    fragment, so it never reaches a server log, a proxy log or a ``Referer``."""
    return f"{app_origin}{INVITE_PATH}#token={raw_token}"


async def _roles_of(
    session: AsyncSession, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    ids = list(user_ids)
    roles: defaultdict[uuid.UUID, list[str]] = defaultdict(list)
    if not ids:
        return roles
    rows = await session.execute(
        select(UserRole.user_id, Role.code)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id.in_(ids))
        .order_by(Role.code)
    )
    for user_id, code in rows:
        roles[user_id].append(code)
    return roles


def _issued(
    user: User, roles: list[str], token: tokens.IssuedToken, app_origin: str
) -> InvitationIssued:
    return InvitationIssued.model_validate(
        {
            "user_id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "roles": sorted(roles),
            "expires_at": token.expires_at,
            "invitation_url": invitation_url(app_origin, token.raw),
        }
    )


def _record_issued(
    session: AsyncSession,
    user_id: uuid.UUID,
    roles: list[str],
    token: tokens.IssuedToken,
    *,
    reissued: bool,
) -> None:
    record_audit_event(
        session,
        action=INVITATION_ISSUED_EVENT,
        record_type=User.__tablename__,
        record_id=user_id,
        details={
            "link_id": token.id,
            "roles": sorted(roles),
            "expires_at": token.expires_at,
            "reissued": reissued,
        },
    )


async def _username_taken(session: AsyncSession, username: str) -> bool:
    return bool(await session.scalar(select(exists().where(User.username == username))))


async def create_invitation(
    session: AsyncSession,
    *,
    principal: Principal,
    guard: RecentAuthGuard,
    username: str,
    display_name: str,
    role: str,
    app_origin: str,
) -> InvitationIssued:
    """Create a pending user with ``role`` and a new invitation link.

    In this order: ``422 username_invalid`` (normalization), ``403
    permission_denied`` unless the principal may assign ``role``, ``403
    reauth_required`` for a staff role without a recent reauthentication,
    ``409 username_taken`` when any user (in any state) has the username,
    also when a concurrent request inserts it first.
    """
    normalized = service.normalize_username(username)
    if not service.may_assign_role(principal.permissions, role):
        raise PermissionDeniedError()
    if role in STAFF_ROLES:
        guard.ensure()
    if await _username_taken(session, normalized):
        raise UsernameTakenError()
    user = User(
        id=uuid.uuid7(), username=normalized, display_name=display_name, state=PENDING
    )
    try:
        # A savepoint: a concurrent invitation that inserted the same username
        # first is 409, not 500.
        async with session.begin_nested():
            session.add(user)
    except IntegrityError as exc:
        if _USERNAME_UNIQUE in str(exc.orig):
            raise UsernameTakenError() from None
        raise
    await service.add_user_role(session, user_id=user.id, role=role)
    token = await tokens.issue_account_token(
        session, user_id=user.id, kind=tokens.INVITATION, issued_by=principal.user_id
    )
    _record_issued(session, user.id, [role], token, reissued=False)
    return _issued(user, [role], token, app_origin)


def _has_invitation_token() -> object:
    return exists().where(
        AccountToken.user_id == User.id, AccountToken.kind == tokens.INVITATION
    )


def _open_invitation_filter() -> object:
    """Pending, no password, and at least one invitation token (D4)."""
    return and_(
        User.state == PENDING,
        User.password_hash.is_(None),
        _has_invitation_token(),  # type: ignore[arg-type]
    )


async def reissue_invitation(
    session: AsyncSession,
    *,
    principal: Principal,
    guard: RecentAuthGuard,
    user_id: uuid.UUID,
    app_origin: str,
) -> InvitationIssued:
    """Replace the open invitation's link with a new one.

    ``404 not_found`` unless ``user_id`` is an open invitation; ``403
    permission_denied`` unless the principal may assign every role the user
    holds; ``403 reauth_required`` when one of them is a staff role and the
    session has no recent reauthentication. The earlier link stops working.
    """
    # Locked: two reissues of the same invitation run one after the other.
    user: User | None = await session.scalar(
        select(User)
        .where(User.id == user_id, _open_invitation_filter())  # type: ignore[arg-type]
        .with_for_update()
    )
    if user is None:
        raise RecordNotFoundError()
    roles = (await _roles_of(session, [user.id]))[user.id]
    if not all(service.may_assign_role(principal.permissions, r) for r in roles):
        raise PermissionDeniedError()
    if STAFF_ROLES.intersection(roles):
        guard.ensure()
    token = await tokens.issue_account_token(
        session, user_id=user.id, kind=tokens.INVITATION, issued_by=principal.user_id
    )
    _record_issued(session, user.id, roles, token, reissued=True)
    return _issued(user, roles, token, app_origin)


async def list_invitations(
    session: AsyncSession, page: OffsetParams
) -> OffsetPage[InvitationListItem]:
    """The open invitations, latest link first (then by user id).

    ``status`` is ``active`` while the latest link is unused, not replaced and
    not expired, otherwise ``expired``. Holds no token or hash.
    """
    latest = (
        select(
            AccountToken.user_id,
            AccountToken.expires_at,
            AccountToken.used_at,
            AccountToken.revoked_at,
            AccountToken.created_at,
        )
        .where(AccountToken.kind == tokens.INVITATION)
        .ext(distinct_on(AccountToken.user_id))
        .order_by(
            AccountToken.user_id,
            AccountToken.created_at.desc(),
            AccountToken.id.desc(),
        )
        .subquery("latest")
    )
    base = (
        select(
            User,
            latest.c.expires_at,
            latest.c.used_at,
            latest.c.revoked_at,
            latest.c.created_at,
        )
        .join(latest, latest.c.user_id == User.id)
        .where(User.state == PENDING, User.password_hash.is_(None))
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = (
        await session.execute(
            base.order_by(latest.c.created_at.desc(), User.id)
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    roles = await _roles_of(session, (row.User.id for row in rows))
    now = clock.now()
    items = [
        InvitationListItem.model_validate(
            {
                "user_id": row.User.id,
                "username": row.User.username,
                "display_name": row.User.display_name,
                "roles": roles[row.User.id],
                "status": (
                    "active"
                    if row.used_at is None
                    and row.revoked_at is None
                    and now < row.expires_at
                    else "expired"
                ),
                "expires_at": row.expires_at,
                "issued_at": row.created_at,
            }
        )
        for row in rows
    ]
    return OffsetPage[InvitationListItem](
        items=items, total=total or 0, limit=page.limit, offset=page.offset
    )


async def _invited_user(
    session: AsyncSession, token: AccountToken, *, lock: bool
) -> User:
    """The token's user, or ``410`` unless it is still pending without a
    password (archived, already active, or a password already set)."""
    statement = select(User).where(User.id == token.user_id)
    if lock:
        statement = statement.with_for_update()
    user: User | None = await session.scalar(statement)
    if user is None or user.state != PENDING or user.password_hash is not None:
        raise TokenInvalidError()
    return user


async def lookup_invitation(session: AsyncSession, raw_token: str) -> InvitationLookup:
    """Who a valid invitation link is for, without changing anything.

    ``410 token_invalid`` for any token that could not be redeemed now.
    """
    token = await tokens.peek_account_token(
        session, raw_token=raw_token, kind=tokens.INVITATION
    )
    user = await _invited_user(session, token, lock=False)
    roles = (await _roles_of(session, [user.id]))[user.id]
    return InvitationLookup.model_validate(
        {
            "username": user.username,
            "display_name": user.display_name,
            "roles": roles,
            "expires_at": token.expires_at,
        }
    )


async def redeem_invitation(
    session: AsyncSession, *, raw_token: str, password: str
) -> None:
    """Set the invited user's password and activate them; the link is used.

    In one transaction: lock the token (``410`` when invalid) and the user
    (``410`` unless still pending without a password), apply the password
    policy (``422``; the caller's rollback leaves the link usable), move the
    user ``pending → active`` (the single activation path, where #35 adds the
    minor-consent check), mark the token used and write
    ``identity.invitation_redeemed``. Creates no session.
    """
    token = await tokens.consume_account_token(
        session, raw_token=raw_token, kind=tokens.INVITATION
    )
    user = await _invited_user(session, token, lock=True)
    await service.set_password(session, user_id=user.id, new_password=password)
    await service.change_user_state(session, user_id=user.id, new_state=ACTIVE)
    await tokens.mark_used(session, token)
    record_audit_event(
        session,
        action=INVITATION_REDEEMED_EVENT,
        record_type=User.__tablename__,
        record_id=user.id,
        details={"link_id": token.id},
    )
