"""Single-use account tokens (#29; ADR-0007 D11).

An account token proves that its holder was given a one-time link: an
invitation (#29) or a password reset (#30). The rules:

- 32 random bytes (``secrets.token_bytes``), shown **once** as unpadded
  base64url (43 characters) in a link; only ``sha256(token)`` is stored
  (``account_tokens.token_hash``).
- Valid for :data:`TOKEN_LIFETIME` (24 hours, a code constant fixed by
  ADR-0007 D11, not a setting): valid while ``clock.now() < expires_at``.
- Issuing a token sets ``revoked_at`` on every earlier unused, unreplaced
  token of the same user and kind, so at most one is live (the partial unique
  index enforces it in the database).
- Used once: the caller sets ``used_at`` (:func:`mark_used`) after its own
  checks, in the same transaction as the change the token authorizes.
- Anything else is :class:`~app.modules.identity.errors.TokenInvalidError`
  (``410 token_invalid``), one body for every case.

Every function runs in the caller's Unit of Work and never commits. Every
time comes from :func:`app.core.clock.now`. The raw token exists only in the
:class:`IssuedToken` returned by :func:`issue_account_token` and in the one
HTTP response that shows it: it is never logged, audited or stored.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.modules.identity import sessions
from app.modules.identity.errors import TokenInvalidError
from app.modules.identity.models import ACCOUNT_TOKEN_KINDS, AccountToken

TOKEN_LIFETIME: Final = timedelta(hours=24)
"""How long an account token is valid (ADR-0007 D11; not a setting)."""

INVITATION: Final = "invitation"
PASSWORD_RESET: Final = "password_reset"  # noqa: S105

__all__ = [
    "INVITATION",
    "PASSWORD_RESET",
    "TOKEN_LIFETIME",
    "IssuedToken",
    "TokenInvalidError",
    "consume_account_token",
    "decode_account_token",
    "issue_account_token",
    "mark_used",
    "peek_account_token",
]


@dataclass(frozen=True)
class IssuedToken:
    """A newly issued token. ``raw`` is the only copy of the token."""

    id: uuid.UUID
    raw: str = field(repr=False)
    expires_at: datetime


def _checked_kind(kind: str) -> str:
    if kind not in ACCOUNT_TOKEN_KINDS:
        raise ValueError(f"not an account token kind: {kind!r}")
    return kind


async def issue_account_token(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: str,
    issued_by: uuid.UUID | None,
) -> IssuedToken:
    """Replace the user's live token of ``kind`` (if any) with a new one.

    Sets ``revoked_at`` on every unused, unreplaced token of the same user and
    kind (expired or not), then stores the hash of a new random token that
    expires :data:`TOKEN_LIFETIME` from now. Returns the raw token.
    """
    _checked_kind(kind)
    now = clock.now()
    await session.execute(
        update(AccountToken)
        .where(
            AccountToken.user_id == user_id,
            AccountToken.kind == kind,
            AccountToken.used_at.is_(None),
            AccountToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    token = sessions.new_token()
    row = AccountToken(
        id=uuid.uuid7(),
        user_id=user_id,
        kind=kind,
        token_hash=sessions.hash_token(token),
        expires_at=now + TOKEN_LIFETIME,
        issued_by_user_id=issued_by,
    )
    session.add(row)
    await session.flush()
    return IssuedToken(
        id=row.id, raw=sessions.encode_token(token), expires_at=row.expires_at
    )


def decode_account_token(raw_token: str) -> bytes:
    """The 32 token bytes of ``raw_token``, or :class:`TokenInvalidError` when
    it is not exactly the canonical unpadded base64url form of 32 bytes."""
    token = sessions.decode_token(raw_token)
    if token is None:
        raise TokenInvalidError()
    return token


def _is_valid(row: AccountToken | None, kind: str) -> bool:
    return (
        row is not None
        and row.kind == kind
        and row.used_at is None
        and row.revoked_at is None
        and clock.now() < row.expires_at
    )


async def _find(
    session: AsyncSession, raw_token: str, kind: str, *, lock: bool
) -> AccountToken:
    _checked_kind(kind)
    token_hash = sessions.hash_token(decode_account_token(raw_token))
    statement = select(AccountToken).where(AccountToken.token_hash == token_hash)
    if lock:
        statement = statement.with_for_update()
    row: AccountToken | None = await session.scalar(statement)
    if row is None or not _is_valid(row, kind):
        raise TokenInvalidError()
    return row


async def peek_account_token(
    session: AsyncSession, *, raw_token: str, kind: str
) -> AccountToken:
    """The valid token row of ``raw_token`` without locking or changing it,
    or :class:`TokenInvalidError` (the same rule as
    :func:`consume_account_token`)."""
    return await _find(session, raw_token, kind, lock=False)


async def consume_account_token(
    session: AsyncSession, *, raw_token: str, kind: str
) -> AccountToken:
    """The valid token row of ``raw_token``, locked (``SELECT … FOR UPDATE``)
    until the caller's transaction ends, or :class:`TokenInvalidError`.

    Valid: the hash exists, the row has ``kind``, ``used_at`` and
    ``revoked_at`` are ``NULL``, and ``clock.now() < expires_at``. The caller
    calls :func:`mark_used` once its own checks pass, in the same transaction.
    Of two concurrent consumers, the second waits for the lock and then sees
    the token used.
    """
    return await _find(session, raw_token, kind, lock=True)


async def mark_used(session: AsyncSession, token: AccountToken) -> None:
    """Set ``used_at`` on a token from :func:`consume_account_token`."""
    token.used_at = clock.now()
    await session.flush()
