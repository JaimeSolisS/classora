"""Password hashing and policy (ADR-0007, D5 and D6).

- Argon2id through ``argon2-cffi`` with the ADR-0007 parameters; hashes are
  PHC strings (``$argon2id$v=19$m=19456,t=2,p=1$...``).
- Every password is normalized with Unicode NFKC before it is hashed, verified
  or checked against the policy, so equivalent spellings (``ﬁ``/``fi``,
  full-width/ASCII digits) are the same password.
- Policy: 12 to 128 code points after NFKC, any characters, not equal to the
  username (case-insensitive). No composition rules.

Plaintext passwords are never logged, put in an exception message or returned.
"""

import unicodedata

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from app.modules.identity.errors import (
    PasswordEqualsUsernameError,
    PasswordTooLongError,
    PasswordTooShortError,
)

MEMORY_COST_KIB = 19456
TIME_COST = 2
PARALLELISM = 1
SALT_LENGTH = 16
HASH_LENGTH = 32

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128

_hasher = PasswordHasher(
    time_cost=TIME_COST,
    memory_cost=MEMORY_COST_KIB,
    parallelism=PARALLELISM,
    hash_len=HASH_LENGTH,
    salt_len=SALT_LENGTH,
    type=Type.ID,
)


def normalize_password(password: str) -> str:
    """The NFKC form that is hashed, verified and measured."""
    return unicodedata.normalize("NFKC", password)


def hash_password(password: str) -> str:
    """A new Argon2id PHC hash of the NFKC-normalized ``password``."""
    return _hasher.hash(normalize_password(password))


def verify_password(stored_hash: str, password: str) -> bool:
    """Whether ``password`` matches ``stored_hash``. Never raises.

    A wrong password and a malformed or foreign stored hash both return False.
    """
    try:
        return _hasher.verify(stored_hash, normalize_password(password))
    except VerificationError, InvalidHashError:
        return False


def needs_rehash(stored_hash: str) -> bool:
    """Whether ``stored_hash`` was made with other parameters (or is unreadable)."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def validate_password(password: str, *, username: str) -> None:
    """Raise a ``PasswordPolicyError`` subclass if ``password`` breaks the policy."""
    normalized = normalize_password(password)
    if len(normalized) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShortError()
    if len(normalized) > MAX_PASSWORD_LENGTH:
        raise PasswordTooLongError()
    if normalized.casefold() == normalize_password(username).casefold():
        raise PasswordEqualsUsernameError()
