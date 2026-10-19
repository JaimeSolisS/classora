"""Session tokens, the session cookie and ``auth_sessions`` rows (#23; ADR-0007 D8).

- **Token:** 32 bytes from :func:`secrets.token_bytes`. The cookie holds it in
  unpadded base64url (43 characters); the database holds only its SHA-256
  hash (``auth_sessions.token_hash``). The raw token is never stored, logged,
  audited or returned in a body: it exists only in the ``Set-Cookie`` header
  of the login response.
- **Cookie** (``Settings.session_cookie_secure``):

  - ``true`` (the default, every deployed environment):
    ``__Host-classora_session`` with ``Secure``, ``HttpOnly``,
    ``SameSite=Lax``, ``Path=/`` and no ``Domain``;
  - ``false`` (allowed only with ``APP_ENV=local``, for plain-HTTP local
    runs): ``classora_session``, the same attributes without ``Secure``.

  ``Max-Age`` is the session's absolute timeout (#25), no ``Expires``. The
  server stays the authority: the cookie lifetime never extends a session.
  Only the current mode's cookie name is read.
- **Limits** (#25, ADR-0007 D8): :func:`session_limits` picks the role
  class. Staff (any of ``STAFF_ROLES``, or no role at all): 30 minutes
  idle, 12 hours absolute. Only tutor, student and/or guardian: 7 days idle,
  30 days absolute. The strictest class wins. Each session stores its own
  ``expires_at`` and ``idle_timeout_seconds`` at login.
- **Expiry:** a session is expired when ``now >= expires_at`` or
  ``now >= last_seen_at + idle_timeout_seconds`` (:func:`is_expired`). It is
  computed, never stored; the hourly purge deletes the row 24 hours later.
- **Activity:** ``last_seen_at`` moves at most once per
  :data:`LAST_SEEN_INTERVAL` (:func:`touch_session`).

Every time comes from :func:`app.core.clock.now`. Every function runs in the
caller's Unit of Work and never commits.
"""

import base64
import hashlib
import re
import secrets
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from fastapi import Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.config import Settings
from app.modules.identity.models import AuthSession
from app.modules.identity.permissions import GUARDIAN, STUDENT, TUTOR

TOKEN_BYTES: Final = 32
TOKEN_LENGTH: Final = 43
"""Characters of an unpadded base64url encoding of :data:`TOKEN_BYTES` bytes."""

SECURE_COOKIE_NAME: Final = "__Host-classora_session"
LOCAL_COOKIE_NAME: Final = "classora_session"

_TOKEN_TEXT = re.compile(rf"[A-Za-z0-9_-]{{{TOKEN_LENGTH}}}")


# --- Limits (#25, ADR-0007 D8) ------------------------------------------------------

SessionClass = Literal["staff", "extended"]


@dataclass(frozen=True)
class SessionLimits:
    """The idle and absolute timeouts of one role class."""

    session_class: SessionClass
    idle: timedelta
    absolute: timedelta


STAFF_LIMITS: Final = SessionLimits(
    "staff", idle=timedelta(minutes=30), absolute=timedelta(hours=12)
)
EXTENDED_LIMITS: Final = SessionLimits(
    "extended", idle=timedelta(days=7), absolute=timedelta(days=30)
)

EXTENDED_ROLES: Final = frozenset({TUTOR, STUDENT, GUARDIAN})
"""The roles of the ``extended`` class; every other role is staff."""

LAST_SEEN_INTERVAL: Final = timedelta(seconds=60)
"""``last_seen_at`` is written at most once per this interval (ADR-0007 D8)."""


def session_limits(roles: Iterable[str]) -> SessionLimits:
    """The limits for a user holding ``roles`` (pure).

    Only tutor, student and/or guardian (:data:`EXTENDED_ROLES`) give
    :data:`EXTENDED_LIMITS`. Any staff role gives :data:`STAFF_LIMITS` (the
    strictest class wins); so does having no role at all, or a code outside
    the catalog (fail safe).
    """
    held = frozenset(roles)
    if held and held <= EXTENDED_ROLES:
        return EXTENDED_LIMITS
    return STAFF_LIMITS


# --- Tokens ---------------------------------------------------------------------


def new_token() -> bytes:
    """A new random session token (32 bytes)."""
    return secrets.token_bytes(TOKEN_BYTES)


def hash_token(token: bytes) -> bytes:
    """The stored form of ``token``: its SHA-256 digest (32 bytes)."""
    return hashlib.sha256(token).digest()


def encode_token(token: bytes) -> str:
    """The cookie value: unpadded base64url."""
    return base64.urlsafe_b64encode(token).rstrip(b"=").decode("ascii")


def decode_token(value: str) -> bytes | None:
    """The token in a cookie value, or ``None`` if it is not exactly the
    canonical unpadded base64url form of 32 bytes."""
    if _TOKEN_TEXT.fullmatch(value) is None:
        return None
    try:
        token = base64.urlsafe_b64decode(value + "=")
    except ValueError:  # binascii.Error is a ValueError
        return None
    # Reject a non-canonical spelling (different trailing bits, same bytes).
    if len(token) != TOKEN_BYTES or encode_token(token) != value:
        return None
    return token


# --- The cookie -----------------------------------------------------------------


def cookie_name(settings: Settings) -> str:
    """The session cookie name of the current mode."""
    return SECURE_COOKIE_NAME if settings.session_cookie_secure else LOCAL_COOKIE_NAME


def set_session_cookie(
    response: Response, settings: Settings, token: bytes, *, max_age: timedelta
) -> None:
    """Add the session cookie holding ``token`` to ``response``.

    ``max_age`` is the session's absolute timeout (``Max-Age``, no
    ``Expires``).
    """
    response.set_cookie(
        cookie_name(settings),
        encode_token(token),
        max_age=int(max_age.total_seconds()),
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    """Tell the browser to drop the session cookie (same name, path and flags)."""
    response.set_cookie(
        cookie_name(settings),
        "",
        max_age=0,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def request_token(request: Request, settings: Settings) -> bytes | None:
    """The token in the request's session cookie (current mode only), if valid."""
    value = request.cookies.get(cookie_name(settings))
    return decode_token(value) if value is not None else None


# --- auth_sessions rows -----------------------------------------------------------


async def create_session(
    session: AsyncSession, user_id: uuid.UUID, limits: SessionLimits
) -> tuple[AuthSession, bytes]:
    """A new live session for ``user_id`` with ``limits`` and its token
    (flushed). ``last_seen_at`` is now; ``expires_at`` is now plus the
    absolute timeout."""
    token = new_token()
    now = clock.now()
    auth_session = AuthSession(
        user_id=user_id,
        token_hash=hash_token(token),
        last_seen_at=now,
        expires_at=now + limits.absolute,
        idle_timeout_seconds=int(limits.idle.total_seconds()),
    )
    session.add(auth_session)
    await session.flush()
    return auth_session, token


async def find_live_session(session: AsyncSession, token: bytes) -> AuthSession | None:
    """The unrevoked session whose hash matches ``token``, expired or not."""
    auth_session: AuthSession | None = await session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_token(token),
            AuthSession.revoked_at.is_(None),
        )
    )
    return auth_session


def is_expired(auth_session: AuthSession, now: datetime) -> bool:
    """Whether ``auth_session`` timed out at ``now`` (idle or absolute)."""
    idle_end = auth_session.last_seen_at + timedelta(
        seconds=auth_session.idle_timeout_seconds
    )
    return now >= auth_session.expires_at or now >= idle_end


async def touch_session(
    session: AsyncSession, auth_session: AuthSession, now: datetime
) -> None:
    """Record activity: ``last_seen_at = now`` if it is at least
    :data:`LAST_SEEN_INTERVAL` old.

    One conditional ``UPDATE`` (so two concurrent requests write at most once),
    and none at all inside the interval. Runs in the request's Unit of Work, so
    a request that fails rolls it back.
    """
    if now - auth_session.last_seen_at < LAST_SEEN_INTERVAL:
        return
    await session.execute(
        update(AuthSession)
        .where(
            AuthSession.id == auth_session.id,
            AuthSession.last_seen_at <= now - LAST_SEEN_INTERVAL,
        )
        .values(last_seen_at=now)
    )


async def revoke_session(session: AsyncSession, auth_session: AuthSession) -> None:
    """Mark ``auth_session`` revoked now (flushed)."""
    auth_session.revoked_at = clock.now()
    await session.flush()


async def revoke_user_sessions(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Revoke every unrevoked session of ``user_id``, expired or not; return
    how many. One ``UPDATE``; other users' sessions are untouched."""
    result = await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=clock.now())
    )
    return int(result.rowcount)  # type: ignore[attr-defined]
