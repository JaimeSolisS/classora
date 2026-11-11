"""Login for the least-privilege runtime role ``classora_app`` (#165).

Migration 0096 creates the role ``NOLOGIN`` and grants it DML only; no secret
ever appears in a migration. ``python -m app.cli sync-runtime-role`` (run by
``make migrate``, ``make e2e`` and the hosts' deploy after
``alembic upgrade head``) then calls :func:`sync_runtime_role` over an owner
connection: it gives the role ``LOGIN`` and the password of
``DATABASE_DIRECT_URL``.

The password is sent as a SCRAM-SHA-256 verifier computed here
(:func:`scram_sha256_verifier`, the format PostgreSQL stores in
``pg_authid.rolpassword``), so the plaintext never appears in a statement, in
``pg_stat_activity`` or in a server log. PostgreSQL recognizes a verifier and
stores it unchanged.
"""

import base64
import hashlib
import hmac
import os
import re
import stringprep
import unicodedata

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.db import RUNTIME_ROLE

# PostgreSQL's defaults (scram_iterations, SCRAM_DEFAULT_SALT_LEN).
SCRAM_ITERATIONS = 4096
SCRAM_SALT_LENGTH = 16

_B64 = r"[A-Za-z0-9+/]+={0,2}"
VERIFIER_PATTERN = re.compile(rf"SCRAM-SHA-256\$[0-9]+:{_B64}\${_B64}:{_B64}")

ROLE_MISSING_MESSAGE = (
    f"The role {RUNTIME_ROLE} does not exist: run `alembic upgrade head` first"
    " (make migrate does)."
)


class RuntimeRoleRefusal(Exception):
    """A refusal with a one-line message that contains no secret."""


def _saslprep(password: str) -> bytes:
    """The password bytes PostgreSQL hashes (its ``pg_saslprep``, RFC 4013).

    An ASCII password is used unchanged. Otherwise the SASLprep profile is
    applied; a password that SASLprep rejects is used as raw UTF-8 bytes,
    exactly as PostgreSQL does.
    """
    raw = password.encode("utf-8")
    if password.isascii():
        return raw
    mapped = "".join(
        " " if stringprep.in_table_c12(char) else char
        for char in password
        if not stringprep.in_table_b1(char)
    )
    normalized = unicodedata.normalize("NFKC", mapped)
    if not normalized:
        return raw
    prohibited = (
        stringprep.in_table_c12,
        stringprep.in_table_c21,
        stringprep.in_table_c22,
        stringprep.in_table_c3,
        stringprep.in_table_c4,
        stringprep.in_table_c5,
        stringprep.in_table_c6,
        stringprep.in_table_c7,
        stringprep.in_table_c8,
        stringprep.in_table_c9,
        stringprep.in_table_a1,
    )
    if any(check(char) for char in normalized for check in prohibited):
        return raw
    if any(stringprep.in_table_d1(char) for char in normalized):
        if any(stringprep.in_table_d2(char) for char in normalized):
            return raw
        if not (
            stringprep.in_table_d1(normalized[0])
            and stringprep.in_table_d1(normalized[-1])
        ):
            return raw
    return normalized.encode("utf-8")


def scram_sha256_verifier(
    password: str, *, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS
) -> str:
    """``SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey>`` (RFC 5802/7677).

    ``salt`` defaults to 16 random bytes; tests pass a fixed one.
    """
    salt = os.urandom(SCRAM_SALT_LENGTH) if salt is None else salt
    salted = hashlib.pbkdf2_hmac("sha256", _saslprep(password), salt, iterations)
    client_key = hmac.digest(salted, b"Client Key", "sha256")
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.digest(salted, b"Server Key", "sha256")

    def b64(value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")

    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def check_runtime_credentials(
    *, direct_user: str | None, direct_password: str | None, migration_user: str | None
) -> str:
    """The runtime password to set, or :class:`RuntimeRoleRefusal`.

    ``direct_*`` come from ``DATABASE_DIRECT_URL`` and ``migration_user`` from
    the URL the command connects with (``DATABASE_MIGRATION_URL``, else the
    direct URL).
    """
    if direct_user != RUNTIME_ROLE:
        raise RuntimeRoleRefusal(
            f"DATABASE_DIRECT_URL must log in as {RUNTIME_ROLE} (see .env.example)."
        )
    if migration_user == direct_user:
        raise RuntimeRoleRefusal(
            "DATABASE_MIGRATION_URL must log in as the database owner, not"
            f" {RUNTIME_ROLE} (see .env.example)."
        )
    if not direct_password:
        raise RuntimeRoleRefusal(
            f"DATABASE_DIRECT_URL must include the password of {RUNTIME_ROLE}."
        )
    return direct_password


async def sync_runtime_role(
    connection: AsyncConnection, *, password: str, salt: bytes | None = None
) -> None:
    """Give ``classora_app`` ``LOGIN`` and ``password`` (idempotent).

    ``connection`` is an owner connection (a superuser, or a role with
    ``CREATEROLE`` and admin rights on ``classora_app``). Raises
    :class:`RuntimeRoleRefusal` when the role is missing. The caller commits.
    """
    exists = await connection.scalar(
        text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"),
        {"role": RUNTIME_ROLE},
    )
    if not exists:
        raise RuntimeRoleRefusal(ROLE_MISSING_MESSAGE)
    verifier = scram_sha256_verifier(password, salt=salt)
    # ALTER ROLE takes no bind parameters. The verifier is checked against its
    # format (base64, '$' and ':' only), so it is a safe literal; it is sent as
    # driver SQL because text() would read its ':' as bind parameters.
    if VERIFIER_PATTERN.fullmatch(verifier) is None:  # pragma: no cover
        raise RuntimeRoleRefusal("Could not compute the password verifier.")
    await connection.exec_driver_sql(
        f"ALTER ROLE {RUNTIME_ROLE} WITH LOGIN PASSWORD '{verifier}'"
    )
