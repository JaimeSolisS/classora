"""Password reset and change (#30; spec §1, §14; ADR-0007 D8, D10, D11, D12, D14).

Three ways to get a new password; in none of them can staff see or choose it.

- **Staff-issued reset** (:func:`issue_password_reset`): an Owner or
  Administrator (``user.password_reset.issue`` plus a recent
  reauthentication, declared on the route) names a username and gets a
  one-time ``password_reset`` link (``app.modules.identity.tokens``), valid
  for 24 hours. Only an Owner (the ``owner`` **role**; a delegation does not
  count) may reset an Owner or an Administrator. Nobody resets their own
  account (they use the self-service change). Only ``active`` users: a reset
  never changes a user's state. The target's sessions stay valid until the
  link is redeemed.
- **Redemption** (:func:`lookup_password_reset`, :func:`redeem_password_reset`):
  the person opens the link and sets a new password. ``set_password`` (#25)
  applies the policy and revokes **every** session of the user. No session is
  created; the person signs in on the login page.
- **Owner recovery from the host shell** (``app.cli reset-owner-password``)
  issues the same kind of link with no actor.
- **Self-service change** (:func:`change_password`): the signed-in user gives
  their current and a new password. Every session ends, a new one replaces the
  current one (rotation, D8), and any live reset link of the user is revoked.

Audit (#22): ``identity.password_reset_issued``,
``identity.password_reset_redeemed``, ``identity.password_changed`` and the
committed ``identity.password_change_failed``. The token row id is stored as
``link_id``. No event holds a password, raw token or hash. A request that
changes nothing (``404``, ``409``, ``410``, ``422``, ``429``) writes no event.

Every function runs in the caller's Unit of Work and never commits, except
:func:`record_failed_change`, which commits its own.
"""

import uuid
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import clock
from app.core.audit import record_audit_event
from app.core.authz import (
    AuthenticationRequiredError,
    PermissionDeniedError,
    Principal,
    RecordNotFoundError,
)
from app.core.ratelimit import RateLimiter
from app.core.uow import UnitOfWork
from app.modules.identity import passwords, service, sessions, tokens
from app.modules.identity.auth import REAUTH_RATE_LIMIT, dummy_password_hash
from app.modules.identity.errors import (
    TokenInvalidError,
    UsernameInvalidError,
    UserNotActiveError,
)
from app.modules.identity.models import AccountToken, Role, User, UserRole
from app.modules.identity.permissions import (
    ACTIVE,
    OWNER_ONLY_RESET_TARGETS,
    may_reset_password_of,
)
from app.modules.identity.schemas import PasswordResetIssued, PasswordResetLookup

PASSWORD_RESET_ISSUED_EVENT: Final = "identity.password_reset_issued"  # noqa: S105
PASSWORD_RESET_REDEEMED_EVENT: Final = "identity.password_reset_redeemed"  # noqa: S105
PASSWORD_CHANGED_EVENT: Final = "identity.password_changed"  # noqa: S105
PASSWORD_CHANGE_FAILED_EVENT: Final = "identity.password_change_failed"  # noqa: S105

RESET_PATH: Final = "/reset"
"""The web page that redeems a reset link (``#token=`` in the fragment)."""

OWNER_ONLY_TARGET_ROLES: Final = OWNER_ONLY_RESET_TARGETS
"""Only an Owner may reset a user holding one of these roles (D12; the rule is
:func:`~app.modules.identity.permissions.may_reset_password_of`, shared with
the user detail page's ``allowed_actions``, #31)."""

ResetSource = Literal["staff", "cli"]


def reset_url(app_origin: str, raw_token: str) -> str:
    """``{APP_ORIGIN}/reset#token=<token>``. The token travels in the fragment,
    so it never reaches a server log, a proxy log or a ``Referer``."""
    return f"{app_origin}{RESET_PATH}#token={raw_token}"


async def _roles_of(session: AsyncSession, user_id: uuid.UUID) -> set[str]:
    rows = await session.scalars(
        select(Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    )
    return set(rows)


async def _user_by_username(session: AsyncSession, raw_username: str) -> User | None:
    """The user with the normalized ``raw_username`` (locked), or ``None``
    when it is not a valid username or no user has it."""
    try:
        username = service.normalize_username(raw_username)
    except UsernameInvalidError:
        return None
    user: User | None = await session.scalar(
        select(User).where(User.username == username).with_for_update()
    )
    return user


async def issue_reset_token(
    session: AsyncSession,
    *,
    user: User,
    issued_by: uuid.UUID | None,
    source: ResetSource,
) -> tokens.IssuedToken:
    """Replace ``user``'s reset link with a new one and write
    ``identity.password_reset_issued``. No authority checks here."""
    token = await tokens.issue_account_token(
        session, user_id=user.id, kind=tokens.PASSWORD_RESET, issued_by=issued_by
    )
    record_audit_event(
        session,
        action=PASSWORD_RESET_ISSUED_EVENT,
        record_type=User.__tablename__,
        record_id=user.id,
        details={
            "link_id": token.id,
            "expires_at": token.expires_at,
            "source": source,
        },
    )
    return token


async def issue_password_reset(
    session: AsyncSession,
    *,
    principal: Principal,
    username: str,
    app_origin: str,
) -> PasswordResetIssued:
    """Issue a one-time reset link for the user named ``username``.

    The route has already checked the permission and the reauthentication.
    Then, in this order: ``404 not_found`` for an invalid or unknown username;
    ``403 permission_denied`` for the caller's own account, and for an Owner
    or Administrator target unless the caller holds the ``owner`` role; ``409
    user_not_active`` unless the target is ``active``. The target's sessions
    stay valid until the link is redeemed.
    """
    user = await _user_by_username(session, username)
    if user is None:
        raise RecordNotFoundError()
    if not may_reset_password_of(
        principal.roles,
        actor_id=principal.user_id,
        target_id=user.id,
        target_roles=await _roles_of(session, user.id),
    ):
        raise PermissionDeniedError()
    if user.state != ACTIVE:
        raise UserNotActiveError()
    token = await issue_reset_token(
        session, user=user, issued_by=principal.user_id, source="staff"
    )
    return PasswordResetIssued.model_validate(
        {
            "user_id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "expires_at": token.expires_at,
            "reset_url": reset_url(app_origin, token.raw),
        }
    )


async def _reset_user(
    session: AsyncSession, token: AccountToken, *, lock: bool
) -> User:
    """The token's user, or ``410`` unless ``active`` (a reset never
    reactivates anyone)."""
    statement = select(User).where(User.id == token.user_id)
    if lock:
        statement = statement.with_for_update()
    user: User | None = await session.scalar(statement)
    if user is None or user.state != ACTIVE:
        raise TokenInvalidError()
    return user


async def lookup_password_reset(
    session: AsyncSession, raw_token: str
) -> PasswordResetLookup:
    """Who a valid reset link is for, without changing anything.

    ``410 token_invalid`` for any token that could not be redeemed now.
    """
    token = await tokens.peek_account_token(
        session, raw_token=raw_token, kind=tokens.PASSWORD_RESET
    )
    user = await _reset_user(session, token, lock=False)
    return PasswordResetLookup.model_validate(
        {
            "username": user.username,
            "display_name": user.display_name,
            "expires_at": token.expires_at,
        }
    )


async def redeem_password_reset(
    session: AsyncSession, *, raw_token: str, password: str
) -> None:
    """Set the user's new password from a reset link; the link is used.

    In one transaction: lock the token (``410`` when invalid) and the user
    (``410`` unless ``active``), :func:`~service.set_password` (the policy,
    ``422`` with the link still usable after the caller's rollback, and the
    revocation of every session of the user), mark the token used and write
    ``identity.password_reset_redeemed``. Creates no session.
    """
    token = await tokens.consume_account_token(
        session, raw_token=raw_token, kind=tokens.PASSWORD_RESET
    )
    user = await _reset_user(session, token, lock=True)
    await service.set_password(session, user_id=user.id, new_password=password)
    await tokens.mark_used(session, token)
    record_audit_event(
        session,
        action=PASSWORD_RESET_REDEEMED_EVENT,
        record_type=User.__tablename__,
        record_id=user.id,
        details={"link_id": token.id},
    )


# --- Self-service change ------------------------------------------------------------

ChangeFailureReason = Literal["invalid_password", "no_password"]


@dataclass(frozen=True)
class ChangeFailure:
    """Why a password change failed (audited, never shown to the client)."""

    reason: ChangeFailureReason
    user_id: uuid.UUID
    auth_session_id: uuid.UUID


@dataclass(frozen=True)
class ChangeSuccess:
    user: User
    principal: Principal
    token: bytes
    limits: sessions.SessionLimits


async def check_new_password(
    session: AsyncSession, principal: Principal, new_password: str
) -> None:
    """Apply the password policy to ``new_password`` for the signed-in user
    (``422`` with the policy code). Runs before any rate-limit reservation or
    verification, so a policy mistake costs no attempt."""
    user = await session.get(User, principal.user_id)
    if user is None:  # pragma: no cover - the session was resolved
        raise AuthenticationRequiredError()
    passwords.validate_password(new_password, username=user.username)


async def _revoke_reset_tokens(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Set ``revoked_at`` on the user's live reset link, if any: an
    outstanding staff link must not undo a change the user just made."""
    await session.execute(
        update(AccountToken)
        .where(
            AccountToken.user_id == user_id,
            AccountToken.kind == tokens.PASSWORD_RESET,
            AccountToken.used_at.is_(None),
            AccountToken.revoked_at.is_(None),
        )
        .values(revoked_at=clock.now())
        .execution_options(synchronize_session=False)
    )


async def change_password(
    session: AsyncSession,
    principal: Principal,
    auth_session_id: uuid.UUID,
    *,
    current_password: str,
    new_password: str,
    limiter: RateLimiter,
) -> ChangeSuccess | ChangeFailure:
    """Change the signed-in user's password and rotate the session.

    The caller has applied :func:`check_new_password` and ended the request's
    transaction (``session`` must have none open). First reserves
    ``reauth.user`` for the user (shared with ``/auth/reauthenticate``; ``429``
    when used up: no verification, nothing written). Then exactly one Argon2id
    verification of ``current_password``. On failure writes nothing (the
    reservation stays). On success, in ``session``: ``set_password`` (revokes
    **every** session, the current one included), any live reset link
    revoked, a new session with the user's session class and no
    reauthentication mark, the reservation released, and
    ``identity.password_changed``.
    """
    reservation = await limiter.reserve(REAUTH_RATE_LIMIT, str(principal.user_id))
    user = await session.get(User, principal.user_id)
    if user is None:  # pragma: no cover - the session was resolved
        raise AuthenticationRequiredError()
    stored = user.password_hash
    matches = passwords.verify_password(
        stored or dummy_password_hash(), current_password
    )
    if stored is None:
        return ChangeFailure("no_password", user.id, auth_session_id)
    if not matches:
        return ChangeFailure("invalid_password", user.id, auth_session_id)
    await service.set_password(session, user_id=user.id, new_password=new_password)
    await _revoke_reset_tokens(session, user.id)
    limits = sessions.session_limits(principal.roles)
    new_session, token = await sessions.create_session(session, user.id, limits)
    await reservation.release(session)
    record_audit_event(
        session,
        action=PASSWORD_CHANGED_EVENT,
        record_type=User.__tablename__,
        record_id=user.id,
        details={"auth_session_id": new_session.id},
    )
    return ChangeSuccess(user=user, principal=principal, token=token, limits=limits)


async def record_failed_change(
    sessionmaker: async_sessionmaker[AsyncSession], failure: ChangeFailure
) -> None:
    """Write ``identity.password_change_failed`` in its own Unit of Work and
    commit it. The actor is the user. The caller must have released its own
    transaction first (one connection per request at a time)."""
    async with UnitOfWork(sessionmaker, failure.user_id) as uow:
        record_audit_event(
            uow.session,
            action=PASSWORD_CHANGE_FAILED_EVENT,
            record_type=User.__tablename__,
            record_id=failure.user_id,
            details={
                "auth_session_id": failure.auth_session_id,
                "reason": failure.reason,
            },
        )
