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

  No ``Max-Age``/``Expires`` yet: a browser-session cookie (#25 adds the
  lifetime). Only the current mode's cookie name is read.

Every function runs in the caller's Unit of Work and never commits.
"""

import base64
import hashlib
import re
import secrets
import uuid
from typing import Final

from fastapi import Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.identity.models import AuthSession

TOKEN_BYTES: Final = 32
TOKEN_LENGTH: Final = 43
"""Characters of an unpadded base64url encoding of :data:`TOKEN_BYTES` bytes."""

SECURE_COOKIE_NAME: Final = "__Host-classora_session"
LOCAL_COOKIE_NAME: Final = "classora_session"

_TOKEN_TEXT = re.compile(rf"[A-Za-z0-9_-]{{{TOKEN_LENGTH}}}")


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


def set_session_cookie(response: Response, settings: Settings, token: bytes) -> None:
    """Add the session cookie holding ``token`` to ``response``."""
    response.set_cookie(
        cookie_name(settings),
        encode_token(token),
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
    session: AsyncSession, user_id: uuid.UUID
) -> tuple[AuthSession, bytes]:
    """A new live session for ``user_id`` and its token (flushed)."""
    token = new_token()
    auth_session = AuthSession(user_id=user_id, token_hash=hash_token(token))
    session.add(auth_session)
    await session.flush()
    return auth_session, token


async def find_live_session(session: AsyncSession, token: bytes) -> AuthSession | None:
    """The live (not revoked) session whose hash matches ``token``."""
    auth_session: AuthSession | None = await session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_token(token),
            AuthSession.revoked_at.is_(None),
        )
    )
    return auth_session


async def revoke_session(session: AsyncSession, auth_session: AuthSession) -> None:
    """Mark ``auth_session`` revoked (flushed)."""
    auth_session.revoked_at = func.now()
    await session.flush()
