"""Password hashing and policy (ADR-0007, D5 and D6; M2-02 E2-E4)."""

import logging

import pytest
from argon2 import PasswordHasher, Type

from app.modules.identity import passwords
from app.modules.identity.service import (
    PasswordEqualsUsernameError,
    PasswordPolicyError,
    PasswordTooLongError,
    PasswordTooShortError,
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)

PASSWORD = "correct horse battery"
PREFIX = "$argon2id$v=19$m=19456,t=2,p=1$"


@pytest.fixture(scope="module")
def stored() -> str:
    return hash_password(PASSWORD)


# --- E2: hashing --------------------------------------------------------------


def test_parameters_are_the_adr_constants():
    assert passwords.MEMORY_COST_KIB == 19456
    assert passwords.TIME_COST == 2
    assert passwords.PARALLELISM == 1
    assert passwords.SALT_LENGTH == 16
    assert passwords.HASH_LENGTH == 32


def test_hash_is_an_argon2id_phc_string(stored):
    assert stored.startswith(PREFIX)
    salt, digest = stored.removeprefix(PREFIX).split("$")
    # Unpadded base64: 16 bytes -> 22 characters, 32 bytes -> 43 characters.
    assert len(salt) == 22
    assert len(digest) == 43


def test_hashes_are_salted(stored):
    assert hash_password(PASSWORD) != stored


def test_verify_accepts_the_right_password(stored):
    assert verify_password(stored, PASSWORD) is True


@pytest.mark.parametrize(
    "candidate", ["correct horse batterY", "", "correct horse battery "]
)
def test_verify_rejects_a_wrong_password(stored, candidate):
    assert verify_password(stored, candidate) is False


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "not a hash",
        "$argon2id$v=19$m=19456,t=2,p=1$garbage",
        "$argon2id$v=19$m=19456,t=2,p=1$c2FsdHNhbHQ$",
        "$2b$12$abcdefghijklmnopqrstuuJ9Gx1v9QW6m1pS7FfX6e6bW4t1PZr8i",
        "$argon2i$v=19$m=16,t=2,p=1$c2FsdHNhbHQ$AAAAAAAAAAAAAAAAAAAAAA",
    ],
)
def test_verify_never_raises_on_a_malformed_hash(malformed):
    assert verify_password(malformed, PASSWORD) is False


@pytest.mark.parametrize(
    ("hashed", "typed"),
    [
        ("ﬁnancial-records", "financial-records"),  # U+FB01 ligature
        ("financial-records", "ﬁnancial-records"),
        ("pin-１２３４-long", "pin-1234-long"),  # full-width digits
        ("pin-1234-long", "pin-１２３４-long"),
    ],
)
def test_nfkc_equivalent_passwords_verify(hashed, typed):
    assert verify_password(hash_password(hashed), typed) is True


def test_needs_rehash_is_false_for_current_parameters(stored):
    assert needs_rehash(stored) is False


def test_needs_rehash_is_true_for_other_parameters():
    old = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1, type=Type.ID)

    old_hash = old.hash(PASSWORD)

    assert needs_rehash(old_hash) is True
    assert verify_password(old_hash, PASSWORD) is True


def test_needs_rehash_is_true_for_an_unreadable_hash():
    assert needs_rehash("not a hash") is True


# --- E3: policy -------------------------------------------------------------------


@pytest.mark.parametrize("length", [12, 13, 64, 128])
def test_lengths_within_the_policy_pass(length):
    validate_password("x" * length, username="ana")


@pytest.mark.parametrize(
    ("password", "error", "code"),
    [
        ("x" * 11, PasswordTooShortError, "password_too_short"),
        ("", PasswordTooShortError, "password_too_short"),
        ("x" * 129, PasswordTooLongError, "password_too_long"),
    ],
)
def test_length_violations(password, error, code):
    with pytest.raises(error) as caught:
        validate_password(password, username="ana")

    assert caught.value.code == code
    assert isinstance(caught.value, PasswordPolicyError)


@pytest.mark.parametrize(
    "password",
    [
        " " * 12,  # only spaces
        "\U0001f600" * 12,  # only emoji (12 code points)
        "ﬁ" + "x" * 10,  # 11 code points that NFKC expands to 12
    ],
)
def test_any_characters_are_allowed(password):
    validate_password(password, username="ana")


def test_nfkc_expansion_counts_after_normalization():
    assert len("ﬁ" + "x" * 10) == 11
    assert len(passwords.normalize_password("ﬁ" + "x" * 10)) == 12


def test_nfkc_contraction_can_make_a_password_too_short():
    # 12 code points, but NFKC turns "e" + combining acute into one: 11.
    password = "é" + "x" * 10

    with pytest.raises(PasswordTooShortError):
        validate_password(password, username="ana")


@pytest.mark.parametrize(
    ("password", "username"),
    [
        ("ana.lopez.2026", "ana.lopez.2026"),
        ("ANA.LOPEZ.2026", "ana.lopez.2026"),
        ("Ana.Lopez.2026", "ANA.lopez.2026"),
        ("Ａna.lopez.2026", "ana.lopez.2026"),  # full-width A after NFKC
    ],
)
def test_password_equal_to_the_username_is_rejected(password, username):
    with pytest.raises(PasswordEqualsUsernameError) as caught:
        validate_password(password, username=username)

    assert caught.value.code == "password_equals_username"


def test_password_containing_the_username_is_allowed():
    validate_password("ana.lopez.2026!", username="ana.lopez.2026")


# --- E4: the password never leaks -----------------------------------------------


@pytest.mark.parametrize(
    "password", ["leak-me-short", "x" * 11 + "LEAK", "LEAK" * 40, "leakmeleakme"]
)
def test_password_never_appears_in_errors_logs_or_output(password, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    texts: list[str] = []
    for check in (
        lambda: validate_password(password, username="leakmeleakme"),
        lambda: verify_password("not a hash", password),
        lambda: verify_password(hash_password("another-password"), password),
    ):
        try:
            check()
        except PasswordPolicyError as exc:
            texts += [str(exc), repr(exc), exc.detail or "", exc.code]
    stored = hash_password(password)
    texts.append(stored)

    captured = capsys.readouterr()
    for text in [*texts, caplog.text, captured.out, captured.err]:
        assert password not in text
