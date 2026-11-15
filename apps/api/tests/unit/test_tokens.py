"""Account tokens and invitations without a database (#29 C, G4, H1).

- token encoding and the malformed values that are ``410 token_invalid``;
- the lifetime constant and the error classes;
- the conditional reauthentication guard is reached for exactly the four
  staff roles (G4), after the role-authority check.
"""

import base64
import hashlib
import uuid
from datetime import timedelta
from typing import Any

import pytest

from app.core.authz import PermissionDeniedError, Principal
from app.core.errors import AppError
from app.modules.identity import invitations, tokens
from app.modules.identity.errors import TokenInvalidError, UsernameTakenError
from app.modules.identity.permissions import (
    ROLE_CODES,
    ROLE_PERMISSIONS,
    STAFF_ROLES,
)
from app.modules.identity.sessions import encode_token, hash_token

RAW = bytes(range(32))


def test_lifetime_is_24_hours():
    assert timedelta(hours=24) == tokens.TOKEN_LIFETIME


def test_a_token_is_43_unpadded_base64url_characters_and_decodes_back():
    encoded = encode_token(RAW)

    assert len(encoded) == 43
    assert "=" not in encoded
    assert tokens.decode_account_token(encoded) == RAW
    assert hash_token(RAW) == hashlib.sha256(RAW).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty"),
        pytest.param(encode_token(RAW)[:42], id="42-characters"),
        pytest.param(_b64(RAW), id="padded"),
        pytest.param(encode_token(RAW)[:-1] + "+", id="plus"),
        pytest.param(encode_token(RAW)[:-1] + "/", id="slash"),
        pytest.param("!" * 43, id="not-base64url"),
        pytest.param(_b64(bytes(33)).rstrip("="), id="33-bytes"),
        pytest.param(encode_token(RAW)[:-1] + "B", id="non-canonical-last"),
        pytest.param(" " + encode_token(RAW)[1:], id="space"),
    ],
)
def test_malformed_tokens_are_token_invalid(value):
    with pytest.raises(TokenInvalidError):
        tokens.decode_account_token(value)


def test_token_invalid_is_a_fixed_410():
    error = TokenInvalidError()

    assert isinstance(error, AppError)
    assert (error.status, error.code) == (410, "token_invalid")
    assert error.detail == "This link is no longer valid. Ask the center for a new one."
    with pytest.raises(TypeError):
        TokenInvalidError("detail")  # type: ignore[call-arg]


def test_username_taken_is_a_fixed_409():
    error = UsernameTakenError()

    assert (error.status, error.code) == (409, "username_taken")
    assert error.detail == "That username is already taken."
    with pytest.raises(TypeError):
        UsernameTakenError("someone")  # type: ignore[call-arg]


def test_the_issued_token_repr_hides_the_raw_token():
    issued = tokens.IssuedToken(
        id=uuid.uuid7(),
        raw=encode_token(RAW),
        expires_at=None,  # type: ignore[arg-type]
    )

    assert encode_token(RAW) not in repr(issued)


def test_the_invitation_url_carries_the_token_in_the_fragment():
    url = invitations.invitation_url("https://app.example", encode_token(RAW))

    assert url == f"https://app.example/invite#token={encode_token(RAW)}"


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="kind"):
        tokens._checked_kind("other")


def test_the_kinds_are_invitation_and_password_reset():
    # #30 B1: one new kind; the lifetime is the same 24 hours for both.
    assert tokens.INVITATION == "invitation"
    assert tokens.PASSWORD_RESET == "password_reset"
    assert tokens._checked_kind(tokens.PASSWORD_RESET) == "password_reset"
    assert timedelta(hours=24) == tokens.TOKEN_LIFETIME


# --- G4: the conditional guard per role ----------------------------------------------


class GuardReached(Exception):
    pass


class DatabaseReached(Exception):
    pass


class SpyGuard:
    def __init__(self) -> None:
        self.calls = 0

    def ensure(self) -> None:
        self.calls += 1
        raise GuardReached()


class NoDatabase:
    """A session whose first query fails: the check stopped before it."""

    async def scalar(self, *args: Any, **kwargs: Any) -> Any:
        raise DatabaseReached()


OWNER_PRINCIPAL = Principal(
    user_id=uuid.uuid7(),
    roles=frozenset({"owner"}),
    permissions=ROLE_PERMISSIONS["owner"],
)


async def _create(role: str, principal: Principal, guard: SpyGuard) -> None:
    await invitations.create_invitation(
        NoDatabase(),  # type: ignore[arg-type]
        principal=principal,
        guard=guard,  # type: ignore[arg-type]
        username="new.person",
        display_name="New Person",
        role=role,
        app_origin="https://app.example",
    )


@pytest.mark.anyio
@pytest.mark.parametrize("role", ROLE_CODES)
async def test_the_guard_is_reached_for_exactly_the_staff_roles(role):
    guard = SpyGuard()

    expected = GuardReached if role in STAFF_ROLES else DatabaseReached
    with pytest.raises(expected):
        await _create(role, OWNER_PRINCIPAL, guard)

    assert guard.calls == (1 if role in STAFF_ROLES else 0)
    assert STAFF_ROLES == {
        "owner",
        "administrator",
        "academic_coordinator",
        "finance_staff",
    }


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["owner", "administrator"])
async def test_a_role_the_actor_cannot_assign_is_denied_before_the_guard(role):
    administrator = Principal(
        user_id=uuid.uuid7(),
        roles=frozenset({"administrator"}),
        permissions=ROLE_PERMISSIONS["administrator"],
    )
    guard = SpyGuard()

    with pytest.raises(PermissionDeniedError):
        await _create(role, administrator, guard)

    assert guard.calls == 0


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
