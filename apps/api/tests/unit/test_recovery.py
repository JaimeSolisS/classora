"""Password reset and change: pure parts (#30 C3, H2)."""

import pytest

from app.core.errors import AppError
from app.modules.identity import recovery
from app.modules.identity.errors import (
    CurrentPasswordInvalidError,
    UserNotActiveError,
)
from app.modules.identity.schemas import (
    PasswordChange,
    PasswordResetRedeemRequest,
    PasswordResetTokenRequest,
)

TOKEN = "t" * 43


def test_the_reset_url_carries_the_token_in_the_fragment():
    assert (
        recovery.reset_url("https://app.example", TOKEN)
        == f"https://app.example/reset#token={TOKEN}"
    )


def test_current_password_invalid_is_a_fixed_403():
    error = CurrentPasswordInvalidError()

    assert isinstance(error, AppError)
    assert (error.status, error.code) == (403, "current_password_invalid")
    assert error.detail == "The current password is incorrect."
    with pytest.raises(TypeError):
        CurrentPasswordInvalidError("detail")  # type: ignore[call-arg]


def test_user_not_active_is_a_fixed_409():
    error = UserNotActiveError()

    assert isinstance(error, AppError)
    assert (error.status, error.code) == (409, "user_not_active")
    with pytest.raises(TypeError):
        UserNotActiveError("suspended")  # type: ignore[call-arg]


def test_only_an_owner_resets_owners_and_administrators():
    assert frozenset({"owner", "administrator"}) == recovery.OWNER_ONLY_TARGET_ROLES


def test_request_bodies_hide_passwords_and_tokens_in_repr():
    secret = "hunter2-hunter2"
    bodies = [
        PasswordChange(current_password=secret, new_password=secret),
        PasswordResetRedeemRequest(token=secret, password=secret),
        PasswordResetTokenRequest(token=secret),
    ]
    for body in bodies:
        assert secret not in repr(body)


def test_the_event_names():
    assert (
        recovery.PASSWORD_RESET_ISSUED_EVENT,
        recovery.PASSWORD_RESET_REDEEMED_EVENT,
        recovery.PASSWORD_CHANGED_EVENT,
        recovery.PASSWORD_CHANGE_FAILED_EVENT,
    ) == (
        "identity.password_reset_issued",
        "identity.password_reset_redeemed",
        "identity.password_changed",
        "identity.password_change_failed",
    )
