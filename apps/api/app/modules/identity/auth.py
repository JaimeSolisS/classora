"""Login and logout (#23; ADR-0007 D5, D7, D8).

**Login** succeeds only for an ``active`` user with a password whose
Argon2id verification passes. Every attempt runs exactly one verification:
without a user or a stored hash it verifies against :func:`dummy_password_hash`
and ignores the result, so an unknown account costs the same work.

On success, in the caller's Unit of Work: the hash is upgraded when its
parameters changed (D5), any unrevoked session on the request's cookie,
expired or not, is revoked (rotation; a client token is never adopted), a new
session is created with the limits of the user's role class (#25) and
``identity.login_succeeded`` is written.

On failure the caller first rolls the request's transaction back (the
attempt writes nothing, and its connection returns to the pool), then writes
``identity.login_failed`` with :func:`record_failed_login` in its **own**
committed Unit of Work, so a failed login never holds two connections; then
it raises
:class:`~app.modules.identity.errors.InvalidCredentialsError`, which rolls the
request's Unit of Work back. The submitted username is never stored (people
type passwords into it); neither is the password.

**Logout** revokes the request's own session and writes ``identity.logout``.

**Reauthentication** (#26; ADR-0007 D10) checks the signed-in user's password
with exactly one Argon2id verification (against :func:`dummy_password_hash`
when the user has no password). On success it sets ``reauthenticated_at`` of
the **current** session to now and writes ``identity.reauthenticated``; the
hash is not upgraded (login does that). On failure the caller rolls the
request's transaction back and writes ``identity.reauth_failed`` with
:func:`record_failed_reauth` in its own committed Unit of Work, the same
pattern as a failed login; the session and its ``reauthenticated_at`` are
unchanged.

**Rate limits** (#27; ``app.core.ratelimit``). The login route reserves
:data:`LOGIN_USERNAME_RATE_LIMIT` keyed by :func:`login_rate_limit_key`
before :func:`login` runs; :func:`reauthenticate` reserves
:data:`REAUTH_RATE_LIMIT` keyed by the user id before its password check. A
used-up limit raises ``429 rate_limited`` before any verification. A
successful check releases its reservation in the caller's Unit of Work; a
failed one keeps it.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Final, Literal

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import clock
from app.core.audit import record_audit_event
from app.core.authz import AuthenticationRequiredError, Principal
from app.core.config import Settings
from app.core.csrf import csrf_token_for
from app.core.ratelimit import RateLimiter
from app.core.reauth import REAUTH_WINDOW
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.identity import passwords, sessions
from app.modules.identity.errors import UsernameInvalidError
from app.modules.identity.models import AuthSession, User
from app.modules.identity.permissions import ACTIVE
from app.modules.identity.schemas import SessionUser
from app.modules.identity.service import (
    get_user_preferences,
    load_principal,
    normalize_username,
)

LOGIN_SUCCEEDED_EVENT: Final = "identity.login_succeeded"
LOGIN_FAILED_EVENT: Final = "identity.login_failed"
LOGOUT_EVENT: Final = "identity.logout"
REAUTHENTICATED_EVENT: Final = "identity.reauthenticated"
REAUTH_FAILED_EVENT: Final = "identity.reauth_failed"

LOGIN_USERNAME_RATE_LIMIT: Final = "login.username"
REAUTH_RATE_LIMIT: Final = "reauth.user"
"""Shared with the self-service password change's current-password check (#30)."""
INVALID_USERNAME_KEY_PREFIX: Final = "!"
"""Prefix of the rate-limit key of a username :func:`normalize_username`
rejects; ``!`` is never part of a valid username, so the two never share a key."""

FailureReason = Literal[
    "unknown_username", "no_password", "invalid_password", "not_active"
]


@dataclass(frozen=True)
class LoginFailure:
    """Why a login failed (audited, never shown to the client)."""

    reason: FailureReason
    user_id: uuid.UUID | None = None
    state: str | None = None


@dataclass(frozen=True)
class LoginSuccess:
    user: User
    principal: Principal
    token: bytes
    limits: sessions.SessionLimits


@cache
def dummy_password_hash() -> str:
    """A hash of a random password, made once per process with the current
    parameters; verified when there is no stored hash to verify against."""
    return passwords.hash_password(secrets.token_urlsafe(32))


def login_rate_limit_key(raw_username: str) -> str:
    """The ``login.username`` key of a submitted username, existing or not.

    The normalized username; a value that :func:`normalize_username` rejects
    is counted too, under its trimmed, lower-cased form with
    :data:`INVALID_USERNAME_KEY_PREFIX`. Only its hash is stored.
    """
    try:
        return normalize_username(raw_username)
    except UsernameInvalidError:
        return INVALID_USERNAME_KEY_PREFIX + raw_username.strip().lower()


async def _find_user(session: AsyncSession, raw_username: str) -> User | None:
    try:
        username = normalize_username(raw_username)
    except UsernameInvalidError:
        return None
    user: User | None = await session.scalar(
        select(User).where(User.username == username)
    )
    return user


async def check_credentials(
    session: AsyncSession, username: str, password: str
) -> User | LoginFailure:
    """The user the credentials belong to, or why they do not succeed.

    Runs exactly one password verification, whatever the outcome.
    """
    user = await _find_user(session, username)
    stored = user.password_hash if user is not None else None
    matches = passwords.verify_password(stored or dummy_password_hash(), password)
    if user is None:
        return LoginFailure("unknown_username")
    if stored is None:
        return LoginFailure("no_password", user.id)
    if not matches:
        return LoginFailure("invalid_password", user.id)
    if user.state != ACTIVE:
        return LoginFailure("not_active", user.id, user.state)
    if passwords.needs_rehash(stored):
        user.password_hash = passwords.hash_password(password)
    return user


async def login(
    session: AsyncSession,
    request: Request,
    settings: Settings,
    *,
    username: str,
    password: str,
) -> LoginSuccess | LoginFailure:
    """Check the credentials; on success rotate and create the session."""
    checked = await check_credentials(session, username, password)
    if isinstance(checked, LoginFailure):
        return checked
    user = checked
    previous_token = sessions.request_token(request, settings)
    if previous_token is not None:
        # Unrevoked, expired or not (#25 C5).
        previous = await sessions.find_live_session(session, previous_token)
        if previous is not None:
            await sessions.revoke_session(session, previous)
    principal = await load_principal(session, user.id)
    if principal is None:  # pragma: no cover - checked as active above
        raise RuntimeError("an active user has no principal")
    limits = sessions.session_limits(principal.roles)
    auth_session, token = await sessions.create_session(session, user.id, limits)
    record_audit_event(
        session,
        action=LOGIN_SUCCEEDED_EVENT,
        record_type=User.__tablename__,
        record_id=user.id,
        details={"auth_session_id": auth_session.id},
    )
    return LoginSuccess(user=user, principal=principal, token=token, limits=limits)


async def record_failed_login(
    sessionmaker: async_sessionmaker[AsyncSession], failure: LoginFailure
) -> None:
    """Write ``identity.login_failed`` in its own Unit of Work and commit it.

    The caller must have released its own transaction first (one connection
    per request at a time).
    """
    details: dict[str, str] = {"reason": failure.reason}
    if failure.state is not None:
        details["state"] = failure.state
    async with UnitOfWork(sessionmaker) as uow:
        record_audit_event(
            uow.session,
            action=LOGIN_FAILED_EVENT,
            record_type=User.__tablename__ if failure.user_id is not None else None,
            record_id=failure.user_id,
            details=details,
        )


async def logout(
    session: AsyncSession, request: Request, settings: Settings, principal: Principal
) -> None:
    """Revoke the request's own session (the user's others stay valid)."""
    token = sessions.request_token(request, settings)
    current: AuthSession | None = None
    if token is not None:
        current = await sessions.find_live_session(session, token)
    if current is None or current.user_id != principal.user_id:
        raise AuthenticationRequiredError()
    await sessions.revoke_session(session, current)
    record_audit_event(
        session,
        action=LOGOUT_EVENT,
        record_type=User.__tablename__,
        record_id=principal.user_id,
        details={"auth_session_id": current.id},
    )


ReauthFailureReason = Literal["invalid_password", "no_password"]


@dataclass(frozen=True)
class ReauthFailure:
    """Why a reauthentication failed (audited, never shown to the client)."""

    reason: ReauthFailureReason
    user_id: uuid.UUID
    auth_session_id: uuid.UUID


async def reauthenticate(
    session: AsyncSession,
    principal: Principal,
    auth_session_id: uuid.UUID,
    password: str,
    limiter: RateLimiter,
) -> datetime | ReauthFailure:
    """Check ``password`` for the signed-in ``principal``; on success mark the
    session ``auth_session_id`` reauthenticated now and return the end of the
    window (now + :data:`~app.core.reauth.REAUTH_WINDOW`).

    First reserves :data:`REAUTH_RATE_LIMIT` for the user (``429
    rate_limited`` when used up: no verification, nothing written); ``session``
    must have no open transaction then. Runs exactly one password
    verification, whatever the outcome. Writes nothing on failure (the
    reservation stays); a success releases it in ``session``.
    """
    reservation = await limiter.reserve(REAUTH_RATE_LIMIT, str(principal.user_id))
    user = await session.get(User, principal.user_id)
    if user is None:  # pragma: no cover - the session was resolved
        raise AuthenticationRequiredError()
    stored = user.password_hash
    matches = passwords.verify_password(stored or dummy_password_hash(), password)
    if stored is None:
        return ReauthFailure("no_password", user.id, auth_session_id)
    if not matches:
        return ReauthFailure("invalid_password", user.id, auth_session_id)
    now = clock.now()
    if not await sessions.mark_reauthenticated(session, auth_session_id, now):
        # Revoked between the resolver and this update.
        raise AuthenticationRequiredError()
    record_audit_event(
        session,
        action=REAUTHENTICATED_EVENT,
        record_type=User.__tablename__,
        record_id=user.id,
        details={"auth_session_id": auth_session_id},
    )
    await reservation.release(session)
    return now + REAUTH_WINDOW


async def record_failed_reauth(
    sessionmaker: async_sessionmaker[AsyncSession], failure: ReauthFailure
) -> None:
    """Write ``identity.reauth_failed`` in its own Unit of Work and commit it.

    The actor is the user. The caller must have released its own transaction
    first (one connection per request at a time).
    """
    async with UnitOfWork(sessionmaker, failure.user_id) as uow:
        record_audit_event(
            uow.session,
            action=REAUTH_FAILED_EVENT,
            record_type=User.__tablename__,
            record_id=failure.user_id,
            details={
                "auth_session_id": failure.auth_session_id,
                "reason": failure.reason,
            },
        )


async def session_user(
    session: AsyncSession, user: User, principal: Principal, session_token: bytes
) -> SessionUser:
    """The :class:`SessionUser` of ``user``, with the CSRF token of the
    session whose raw token is ``session_token`` (#24), the user's stored
    preferences (#36: one statement) and the center time zone (#37: at most
    one statement, cached for the Unit of Work).

    The one builder of :class:`SessionUser`: login, ``/auth/me`` and the
    password change (#30) all return what it builds.
    """
    preferences = await get_user_preferences(session, user.id)
    # model_validate: the role and permission codes are checked against the
    # RoleCode and PermissionCode enums of the schema (#28 F3).
    return SessionUser.model_validate(
        {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "roles": sorted(principal.roles),
            "permissions": sorted(principal.permissions),
            "csrf_token": csrf_token_for(session_token),
            "privacy_notice_required": not principal.privacy_notice_accepted,
            "preferences": preferences,
            "center_time_zone": await administration.get_center_time_zone(session),
        }
    )


async def current_session_user(
    session: AsyncSession, request: Request, settings: Settings, principal: Principal
) -> SessionUser:
    """The :class:`SessionUser` of the signed-in ``principal``, with the CSRF
    token of the request's own session (recomputed from its cookie)."""
    user = await session.get(User, principal.user_id)
    token = sessions.request_token(request, settings)
    if user is None or token is None:  # pragma: no cover - the session was resolved
        raise AuthenticationRequiredError()
    return await session_user(session, user, principal, token)
