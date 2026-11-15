"""Username normalization (ADR-0007, D7; M2-02 E1)."""

import pytest

from app.modules.identity.service import UsernameInvalidError, normalize_username


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("abc", "abc"),
        ("a" * 64, "a" * 64),
        ("Ana.Lopez", "ana.lopez"),
        ("  ana_lopez-2 ", "ana_lopez-2"),
        ("\tANA\n", "ana"),
        ("a.b", "a.b"),
        ("0-_", "0-_"),
    ],
)
def test_valid_usernames_are_normalized(raw, expected):
    assert normalize_username(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "ab",  # 2 characters
        "a" * 65,  # 65 characters
        "  ab  ",  # 2 after trimming
        "ana lopez",  # inner space
        "ana@example",  # @
        "josé",  # non-ASCII letter
        "Kelvin",  # Kelvin sign: lower() would make it "kelvin"
        "ana b",  # non-breaking space inside
        "",
        "ana+b",
        "ana/b",
    ],
)
def test_invalid_usernames_are_rejected(raw):
    with pytest.raises(UsernameInvalidError) as caught:
        normalize_username(raw)

    assert caught.value.code == "username_invalid"
    assert caught.value.status == 422


def test_kelvin_sign_would_lower_case_to_ascii():
    # Why non-ASCII must be rejected before lower-casing.
    assert "Kelvin".lower() == "kelvin"


def test_error_message_never_contains_the_input():
    with pytest.raises(UsernameInvalidError) as caught:
        normalize_username("secret name")

    assert "secret" not in str(caught.value)
    assert "secret" not in (caught.value.detail or "")
