"""``python -m app.cli sync-runtime-role`` and its SCRAM verifier (#165 AC 8).

No database: a fake engine captures the SQL the command sends. The real
``ALTER ROLE`` and a login with the password are covered by
``tests/integration/test_runtime_role.py``.
"""

import asyncio
import base64
import hashlib
import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app import cli
from app.core.runtime_role import (
    ROLE_MISSING_MESSAGE,
    VERIFIER_PATTERN,
    RuntimeRoleRefusal,
    check_runtime_credentials,
    scram_sha256_verifier,
    sync_runtime_role,
)
from tests.authz.csrf import TEST_APP_ORIGIN

RUNTIME_PASSWORD = "runtime-placeholder-pw"
OWNER_PASSWORD = "owner-placeholder-pw"
RUNTIME_DIRECT = f"postgresql://classora_app:{RUNTIME_PASSWORD}@db:5432/classora"
RUNTIME_POOLED = f"postgresql://classora_app:{RUNTIME_PASSWORD}@pgbouncer:6432/classora"
OWNER_URL = f"postgresql://classora:{OWNER_PASSWORD}@db:5432/classora"

# RFC 7677 section 3: user "user", password "pencil".
RFC_SALT = "W22ZaJ0SNY7soEsUEjb6gQ=="
RFC_NONCE = "rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
RFC_AUTH_MESSAGE = (
    f"n=user,r=rOprNGfwEbeRWgbNEkqO,r={RFC_NONCE},s={RFC_SALT},i=4096,"
    f"c=biws,r={RFC_NONCE}"
).encode()
RFC_CLIENT_PROOF = "dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ="
RFC_SERVER_SIGNATURE = "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="


# --- the verifier -----------------------------------------------------------


def test_the_verifier_matches_the_rfc_7677_vector():
    verifier = scram_sha256_verifier("pencil", salt=base64.b64decode(RFC_SALT))

    header, keys = verifier.rsplit("$", 1)
    assert header == f"SCRAM-SHA-256$4096:{RFC_SALT}"
    stored_key, server_key = (base64.b64decode(part) for part in keys.split(":"))
    # The server's signature in the RFC exchange comes from ServerKey ...
    server_signature = hmac.digest(server_key, RFC_AUTH_MESSAGE, "sha256")
    assert base64.b64encode(server_signature).decode() == RFC_SERVER_SIGNATURE
    # ... and the client's proof recovers a ClientKey whose hash is StoredKey.
    client_signature = hmac.digest(stored_key, RFC_AUTH_MESSAGE, "sha256")
    client_key = bytes(
        a ^ b
        for a, b in zip(
            base64.b64decode(RFC_CLIENT_PROOF), client_signature, strict=True
        )
    )
    assert hashlib.sha256(client_key).digest() == stored_key


def test_the_verifier_has_postgresql_format_and_a_random_salt():
    first = scram_sha256_verifier(RUNTIME_PASSWORD)
    second = scram_sha256_verifier(RUNTIME_PASSWORD)

    for verifier in (first, second):
        assert VERIFIER_PATTERN.fullmatch(verifier)
        assert verifier.startswith("SCRAM-SHA-256$4096:")
        assert RUNTIME_PASSWORD not in verifier
        salt = verifier.split("$")[1].split(":")[1]
        assert len(base64.b64decode(salt)) == 16
    assert first != second


def test_a_non_ascii_password_is_normalized_like_postgresql():
    salt = b"0123456789abcdef"
    # NFKC: the ligature "ﬁ" is "fi"; a non-breaking space maps to a space.
    assert scram_sha256_verifier("conﬁg x", salt=salt) == (
        scram_sha256_verifier("config x", salt=salt)
    )


# --- the refusal rules ------------------------------------------------------


@pytest.mark.parametrize(
    ("direct_user", "direct_password", "migration_user", "fragment"),
    [
        ("classora", RUNTIME_PASSWORD, "postgres", "must log in as classora_app"),
        (None, RUNTIME_PASSWORD, "classora", "must log in as classora_app"),
        ("classora_app", RUNTIME_PASSWORD, "classora_app", "the database owner"),
        ("classora_app", None, "classora", "must include the password"),
        ("classora_app", "", "classora", "must include the password"),
    ],
)
def test_the_credentials_are_refused(
    direct_user, direct_password, migration_user, fragment
):
    with pytest.raises(RuntimeRoleRefusal) as exc_info:
        check_runtime_credentials(
            direct_user=direct_user,
            direct_password=direct_password,
            migration_user=migration_user,
        )

    assert fragment in str(exc_info.value)
    assert RUNTIME_PASSWORD not in str(exc_info.value)


def test_the_runtime_password_is_returned_when_the_roles_differ():
    assert (
        check_runtime_credentials(
            direct_user="classora_app",
            direct_password=RUNTIME_PASSWORD,
            migration_user="classora",
        )
        == RUNTIME_PASSWORD
    )


# --- the SQL sent -----------------------------------------------------------


class FakeConnection:
    """Captures every statement; ``role_exists`` answers the existence check."""

    def __init__(self, *, role_exists: bool = True) -> None:
        self.role_exists = role_exists
        self.statements: list[str] = []
        self.parameters: list[Any] = []

    async def scalar(self, statement: Any, parameters: Any = None) -> bool:
        self.statements.append(str(statement))
        self.parameters.append(parameters)
        return self.role_exists

    async def exec_driver_sql(self, statement: str, parameters: Any = None) -> None:
        self.statements.append(statement)
        self.parameters.append(parameters)


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.disposed = False

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeConnection]:
        yield self.connection

    async def dispose(self) -> None:
        self.disposed = True


def test_the_statement_carries_the_verifier_and_never_the_password():
    connection = FakeConnection()
    salt = b"fixed-salt-16byt"

    asyncio.run(
        sync_runtime_role(connection, password=RUNTIME_PASSWORD, salt=salt)  # type: ignore[arg-type]
    )

    verifier = scram_sha256_verifier(RUNTIME_PASSWORD, salt=salt)
    assert connection.statements[-1] == (
        f"ALTER ROLE classora_app WITH LOGIN PASSWORD '{verifier}'"
    )
    sent = repr((connection.statements, connection.parameters))
    assert RUNTIME_PASSWORD not in sent


def test_a_missing_role_is_refused_before_any_change():
    connection = FakeConnection(role_exists=False)

    with pytest.raises(RuntimeRoleRefusal) as exc_info:
        asyncio.run(sync_runtime_role(connection, password=RUNTIME_PASSWORD))  # type: ignore[arg-type]

    assert str(exc_info.value) == ROLE_MISSING_MESSAGE
    assert not any("ALTER ROLE" in s for s in connection.statements)


# --- the command ------------------------------------------------------------


def set_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    values = {
        "APP_ENV": "local",
        "APP_ORIGIN": TEST_APP_ORIGIN,
        "DATABASE_URL": RUNTIME_POOLED,
        "DATABASE_DIRECT_URL": RUNTIME_DIRECT,
        "DATABASE_MIGRATION_URL": OWNER_URL,
        **env,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def run_command(
    connection: FakeConnection,
) -> tuple[int, FakeEngine | None, list[Any]]:
    engines: list[FakeEngine] = []
    settings_seen: list[Any] = []

    def factory(settings: Any) -> FakeEngine:
        settings_seen.append(settings)
        engine = FakeEngine(connection)
        engines.append(engine)
        return engine

    code = cli.main(["sync-runtime-role"], engine_factory=factory)  # type: ignore[arg-type]
    return code, (engines[0] if engines else None), settings_seen


def assert_no_secret(output: str) -> None:
    for secret in (RUNTIME_PASSWORD, OWNER_PASSWORD, "SCRAM-SHA-256$"):
        assert secret not in output


def test_the_command_sets_login_and_prints_one_line(monkeypatch, capsys):
    set_env(monkeypatch)
    connection = FakeConnection()

    code, engine, _ = run_command(connection)

    captured = capsys.readouterr()
    assert code == 0
    assert engine is not None and engine.disposed
    assert connection.statements[-1].startswith(
        "ALTER ROLE classora_app WITH LOGIN PASSWORD 'SCRAM-SHA-256$4096:"
    )
    assert captured.out.strip().splitlines() == [
        "The role classora_app can log in with the DATABASE_DIRECT_URL password."
    ]
    assert_no_secret(captured.out + captured.err)


@pytest.mark.parametrize(
    ("env", "fragment"),
    [
        (
            {
                "DATABASE_DIRECT_URL": OWNER_URL,
                "DATABASE_URL": OWNER_URL.replace("db:5432", "pgbouncer:6432"),
            },
            "DATABASE_DIRECT_URL must log in as classora_app",
        ),
        (
            {"DATABASE_MIGRATION_URL": RUNTIME_DIRECT},
            "DATABASE_MIGRATION_URL must log in as the database owner",
        ),
        ({"DATABASE_MIGRATION_URL": ""}, "the database owner"),
    ],
)
def test_the_command_refuses_wrong_urls_without_connecting(
    monkeypatch, capsys, env, fragment
):
    set_env(monkeypatch, **env)
    connection = FakeConnection()

    code, engine, _ = run_command(connection)

    captured = capsys.readouterr()
    assert code == 1
    assert engine is None
    assert fragment in captured.err
    assert_no_secret(captured.out + captured.err)


def test_the_command_refuses_a_missing_role(monkeypatch, capsys):
    set_env(monkeypatch)

    code, engine, _ = run_command(FakeConnection(role_exists=False))

    captured = capsys.readouterr()
    assert code == 1
    assert engine is not None and engine.disposed
    assert "run `alembic upgrade head` first" in captured.err
    assert_no_secret(captured.out + captured.err)


def test_the_command_connects_with_the_owner_url(monkeypatch):
    set_env(monkeypatch)

    _, _, settings_seen = run_command(FakeConnection())

    # create_seed_engine(settings) reads the migration URL (#165 E1).
    from app.core.db import async_migration_database_url

    assert async_migration_database_url(settings_seen[0]).username == "classora"
