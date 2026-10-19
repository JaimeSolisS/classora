"""``account_tokens`` and ``app.modules.identity.tokens`` (#29 B4, C4).

Raw SQL checks of the table's constraints, then the token rules with the
``clock`` fixture: the 24-hour lifetime, replacement, single use, malformed
values, two concurrent consumers, and that only the hash is stored.
"""

import asyncio
import hashlib
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.identity import tokens
from app.modules.identity.errors import TokenInvalidError
from app.modules.identity.models import AccountToken
from app.modules.identity.sessions import decode_token, encode_token
from tests.conftest import CLOCK_START
from tests.integration.auth_support import CreatedUser, create_user
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import in_uow

INSERT = (
    "INSERT INTO account_tokens (user_id, kind, token_hash, expires_at, used_at,"
    " revoked_at) VALUES (:user_id, :kind, :hash, :expires, :used, :revoked)"
)
EXPIRES = CLOCK_START + timedelta(hours=24)


@pytest.fixture
def user(seeded_url: str) -> CreatedUser:
    return create_user(seeded_url, state="pending", password_hash=None)


def token_row(user: CreatedUser, **overrides: Any) -> dict[str, Any]:
    return {
        "user_id": user.id,
        "kind": "invitation",
        "hash": uuid.uuid4().bytes * 2,
        "expires": EXPIRES,
        "used": None,
        "revoked": None,
        **overrides,
    }


# --- B4: constraints -------------------------------------------------------------


def test_a_second_live_token_for_the_same_user_and_kind_is_refused(seeded_url, user):
    run_sql(seeded_url, INSERT, token_row(user))

    with pytest.raises(IntegrityError, match="uq_account_tokens_live_user_kind"):
        run_sql(seeded_url, INSERT, token_row(user))


@pytest.mark.parametrize("ended", ["used", "revoked"])
def test_a_new_live_token_is_accepted_once_the_first_is_used_or_revoked(
    seeded_url, user, ended
):
    run_sql(seeded_url, INSERT, token_row(user, **{ended: CLOCK_START}))

    run_sql(seeded_url, INSERT, token_row(user))

    rows = run_sql(seeded_url, "SELECT count(*) FROM account_tokens")
    assert rows[0][0] == 2


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        pytest.param({"hash": bytes(31)}, "token_hash_length", id="31-byte-hash"),
        pytest.param({"kind": "password_reset"}, "kind_valid", id="password-reset"),
        pytest.param(
            {"used": CLOCK_START, "revoked": CLOCK_START},
            "used_or_revoked",
            id="used-and-revoked",
        ),
    ],
)
def test_invalid_rows_are_refused(seeded_url, user, overrides, constraint):
    with pytest.raises(IntegrityError, match=constraint):
        run_sql(seeded_url, INSERT, token_row(user, **overrides))


# --- C4: the token rules -----------------------------------------------------------


def issue(url: str, user_id: uuid.UUID) -> tokens.IssuedToken:
    return in_uow(
        url,
        lambda s: tokens.issue_account_token(
            s, user_id=user_id, kind=tokens.INVITATION, issued_by=None
        ),
    )


def peek(url: str, raw: str) -> AccountToken:
    return in_uow(
        url,
        lambda s: tokens.peek_account_token(s, raw_token=raw, kind=tokens.INVITATION),
    )


def consume_and_use(url: str, raw: str) -> AccountToken:
    async def _call(session: Any) -> AccountToken:
        token = await tokens.consume_account_token(
            session, raw_token=raw, kind=tokens.INVITATION
        )
        await tokens.mark_used(session, token)
        return token

    return in_uow(url, _call)


def test_a_token_is_valid_until_just_before_24_hours(seeded_url, user, clock):
    issued = issue(seeded_url, user.id)
    assert issued.expires_at == EXPIRES
    assert len(issued.raw) == 43

    clock.set(CLOCK_START + timedelta(hours=23, minutes=59, seconds=59))
    assert peek(seeded_url, issued.raw).id == issued.id

    clock.set(CLOCK_START + timedelta(hours=24))
    with pytest.raises(TokenInvalidError):
        peek(seeded_url, issued.raw)
    with pytest.raises(TokenInvalidError):
        consume_and_use(seeded_url, issued.raw)


def test_issuing_again_replaces_the_earlier_token(seeded_url, user, clock):
    first = issue(seeded_url, user.id)
    clock.advance(timedelta(minutes=1))

    second = issue(seeded_url, user.id)

    [row] = run_sql(
        seeded_url,
        "SELECT revoked_at, used_at FROM account_tokens WHERE id = :id",
        {"id": first.id},
    )
    assert row.revoked_at == CLOCK_START + timedelta(minutes=1)
    assert row.used_at is None
    with pytest.raises(TokenInvalidError):
        peek(seeded_url, first.raw)
    assert peek(seeded_url, second.raw).id == second.id


def test_issuing_again_also_replaces_an_expired_token(seeded_url, user, clock):
    first = issue(seeded_url, user.id)
    clock.advance(timedelta(hours=25))

    issue(seeded_url, user.id)

    [row] = run_sql(
        seeded_url,
        "SELECT revoked_at FROM account_tokens WHERE id = :id",
        {"id": first.id},
    )
    assert row.revoked_at == CLOCK_START + timedelta(hours=25)


def test_a_used_token_is_invalid(seeded_url, user, clock):
    issued = issue(seeded_url, user.id)

    used = consume_and_use(seeded_url, issued.raw)

    assert used.used_at == CLOCK_START
    with pytest.raises(TokenInvalidError):
        peek(seeded_url, issued.raw)
    with pytest.raises(TokenInvalidError):
        consume_and_use(seeded_url, issued.raw)


def test_peeking_changes_nothing(seeded_url, user, clock):
    issued = issue(seeded_url, user.id)
    before = run_sql(seeded_url, "SELECT * FROM account_tokens")

    peek(seeded_url, issued.raw)
    peek(seeded_url, issued.raw)

    assert run_sql(seeded_url, "SELECT * FROM account_tokens") == before


RAW = bytes(range(32))


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty"),
        pytest.param(encode_token(RAW)[:42], id="42-characters"),
        pytest.param(encode_token(RAW) + "=", id="padded"),
        pytest.param("*" * 43, id="non-base64url"),
        pytest.param(encode_token(bytes(33)), id="33-bytes"),
        pytest.param(encode_token(RAW), id="unknown"),
    ],
)
def test_malformed_and_unknown_values_are_invalid(seeded_url, user, value):
    issue(seeded_url, user.id)

    with pytest.raises(TokenInvalidError):
        peek(seeded_url, value)
    with pytest.raises(TokenInvalidError):
        consume_and_use(seeded_url, value)


def test_of_two_concurrent_consumers_exactly_one_succeeds(seeded_url, user, clock):
    issued = issue(seeded_url, user.id)

    async def consume(sessionmaker: Any) -> str:
        try:
            async with UnitOfWork(sessionmaker) as uow:
                token = await tokens.consume_account_token(
                    uow.session, raw_token=issued.raw, kind=tokens.INVITATION
                )
                # Give the other task a chance to reach its lock while this
                # one holds the row.
                await asyncio.sleep(0)
                await tokens.mark_used(uow.session, token)
        except TokenInvalidError:
            return "invalid"
        return "used"

    async def run() -> list[str]:
        engine = short_lived_engine(seeded_url)
        try:
            sessionmaker = create_sessionmaker(engine)
            return list(
                await asyncio.gather(consume(sessionmaker), consume(sessionmaker))
            )
        finally:
            await engine.dispose()

    assert sorted(asyncio.run(run())) == ["invalid", "used"]
    rows = run_sql(seeded_url, "SELECT used_at FROM account_tokens")
    assert [row.used_at for row in rows] == [CLOCK_START]


def test_only_the_sha256_hash_is_stored(seeded_url, user, clock):
    issued = issue(seeded_url, user.id)
    token = decode_token(issued.raw)
    assert token is not None

    [row] = run_sql(
        seeded_url,
        "SELECT token_hash, row_to_json(t)::text AS everything"
        " FROM account_tokens t WHERE id = :id",
        {"id": issued.id},
    )
    assert bytes(row.token_hash) == hashlib.sha256(token).digest()
    assert issued.raw not in row.everything
    assert token.hex() not in row.everything


def test_a_kind_outside_the_catalog_is_refused(seeded_url, user):
    with pytest.raises(ValueError, match="kind"):
        in_uow(
            seeded_url,
            lambda s: tokens.issue_account_token(
                s, user_id=user.id, kind="password_reset", issued_by=None
            ),
        )
