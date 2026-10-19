"""Login and logout (#23; ADR-0007 D5, D7, D8).

**Login** succeeds only for an ``active`` user with a password whose
Argon2id verification passes. Every attempt runs exactly one verification:
without a user or a stored hash it verifies against :func:`dummy_password_hash`
and ignores the result, so an unknown account costs the same work.

On success, in the caller's Unit of Work: the hash is upgraded when its
parameters changed (D5), a live session on the request's cookie is revoked
(rotation; a client token is never adopted), a new session is created and
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
"""

import secrets
import uuid
from dataclasses import dataclass
from functools import cache
from typing import Final, Literal

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import record_audit_event
from app.core.authz import AuthenticationRequiredError, Principal
from app.core.config import Settings
from app.core.csrf import csrf_token_for
from app.core.uow import UnitOfWork
from app.modules.identity import passwords, sessions
from app.modules.identity.errors import UsernameInvalidError
from app.modules.identity.models import AuthSession, User
from app.modules.identity.permissions import ACTIVE
from app.modules.identity.schemas import SessionUser
from app.modules.identity.service import load_principal, normalize_username

LOGIN_SUCCEEDED_EVENT: Final = "identity.login_succeeded"
LOGIN_FAILED_EVENT: Final = "identity.login_failed"
LOGOUT_EVENT: Final = "identity.logout"

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


@cache
def dummy_password_hash() -> str:
    """A hash of a random password, made once per process with the current
    parameters; verified when there is no stored hash to verify against."""
    return passwords.hash_password(secrets.token_urlsafe(32))


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
        previous = await sessions.find_live_session(session, previous_token)
        if previous is not None:
            await sessions.revoke_session(session, previous)
    auth_session, token = await sessions.create_session(session, user.id)
    record_audit_event(
        session,
        action=LOGIN_SUCCEEDED_EVENT,
        record_type=User.__tablename__,
        record_id=user.id,
        details={"auth_session_id": auth_session.id},
    )
    principal = await load_principal(session, user.id)
    if principal is None:  # pragma: no cover - checked as active above
        raise RuntimeError("an active user has no principal")
    return LoginSuccess(user=user, principal=principal, token=token)


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


def session_user(user: User, principal: Principal, session_token: bytes) -> SessionUser:
    """The :class:`SessionUser` of ``user``, with the CSRF token of the
    session whose raw token is ``session_token`` (#24)."""
    return SessionUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        roles=sorted(principal.roles),
        permissions=sorted(principal.permissions),
        csrf_token=csrf_token_for(session_token),
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
    return session_user(user, principal, token)
