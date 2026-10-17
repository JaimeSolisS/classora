"""Typed settings: validation, secret masking, startup failure and .env.example drift.

The autouse ``isolated_settings_env`` fixture (tests/conftest.py) removes every
Settings variable from the environment first, so each test sets exactly what it
needs regardless of the developer's shell.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import (
    ConfigurationError,
    Settings,
    env_var_names,
    get_settings,
    load_settings,
)

API_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = API_ROOT.parents[1] / ".env.example"

DB_PASSWORD = "Db-S3cret-pw-77"
DB_USER = "cfg_user"
VALID_DSN = f"postgresql://{DB_USER}:{DB_PASSWORD}@pooler.example:6432/classora"
DIRECT_PASSWORD = "Direct-S3cret-pw-55"
DIRECT_DSN = f"postgresql://{DB_USER}:{DIRECT_PASSWORD}@db.example:5432/classora"
STORAGE_SECRET = "Storage-S3cret-key-88"

DATABASE_URL_NAMES = ["DATABASE_URL", "DATABASE_DIRECT_URL"]
MINIMAL_ENV = {
    "APP_ENV": "local",
    "DATABASE_URL": VALID_DSN,
    "DATABASE_DIRECT_URL": DIRECT_DSN,
}
FULL_ENV = {
    **MINIMAL_ENV,
    "APP_ENV": "prod",
    "SESSION_COOKIE_SECURE": "true",
    "STORAGE_BUCKET": "classora-docs",
    "STORAGE_REGION": "us-east-1",
    "STORAGE_ENDPOINT_URL": "http://storage:9000",
    "STORAGE_ACCESS_KEY_ID": "access-key-id",
    "STORAGE_SECRET_ACCESS_KEY": STORAGE_SECRET,
}
SECRETS = (VALID_DSN, DB_PASSWORD, DIRECT_DSN, DIRECT_PASSWORD, STORAGE_SECRET)

# Variables that the Compose files interpolate from .env; not read by the API.
COMPOSE_ONLY_VARIABLES = {
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "POSTGRES_PORT",
    "PGBOUNCER_PORT",
}


def set_env(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def config_error(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> str:
    set_env(monkeypatch, env)
    with pytest.raises(ConfigurationError) as exc_info:
        load_settings()
    message = str(exc_info.value)
    assert message.startswith("Configuration error")
    return message


def line_for(message: str, name: str) -> str:
    lines = [line for line in message.splitlines() if line.strip().startswith(name)]
    assert lines, f"{name} not reported in:\n{message}"
    return lines[0]


# --- valid ------------------------------------------------------------------


def test_minimal_environment_loads_with_defaults(monkeypatch):
    set_env(monkeypatch, MINIMAL_ENV)

    settings = load_settings()

    assert settings.app_env == "local"
    assert settings.database_url.get_secret_value() == VALID_DSN
    assert settings.database_direct_url.get_secret_value() == DIRECT_DSN
    assert settings.session_cookie_secure is True
    assert settings.storage_bucket is None
    assert settings.storage_region is None
    assert settings.storage_endpoint_url is None
    assert settings.storage_access_key_id is None
    assert settings.storage_secret_access_key is None


def test_full_environment_loads(monkeypatch):
    set_env(monkeypatch, FULL_ENV)

    settings = load_settings()

    assert settings.app_env == "prod"
    assert settings.session_cookie_secure is True
    assert settings.storage_bucket == "classora-docs"
    assert settings.storage_region == "us-east-1"
    assert str(settings.storage_endpoint_url).startswith("http://storage:9000")
    assert settings.storage_access_key_id == "access-key-id"
    assert settings.storage_secret_access_key is not None
    assert settings.storage_secret_access_key.get_secret_value() == STORAGE_SECRET


@pytest.mark.parametrize("name", ["STORAGE_BUCKET", "STORAGE_ENDPOINT_URL"])
def test_empty_optional_value_counts_as_unset(monkeypatch, name):
    set_env(monkeypatch, {**MINIMAL_ENV, name: ""})

    settings = load_settings()

    assert getattr(settings, name.lower()) is None


def test_unrelated_variables_are_ignored(monkeypatch):
    set_env(
        monkeypatch,
        {**MINIMAL_ENV, "POSTGRES_USER": "x", "SOMETHING_ELSE": "y", "APP_DEBUG": "1"},
    )

    settings = load_settings()

    assert settings.app_env == "local"


def test_get_settings_is_cached(monkeypatch):
    set_env(monkeypatch, MINIMAL_ENV)

    assert get_settings() is get_settings()


# --- missing ----------------------------------------------------------------


@pytest.mark.parametrize("name", ["APP_ENV", *DATABASE_URL_NAMES])
def test_missing_required_variable(monkeypatch, name):
    env = {k: v for k, v in MINIMAL_ENV.items() if k != name}

    message = config_error(monkeypatch, env)

    assert "missing" in line_for(message, name)
    for secret in SECRETS:
        assert secret not in message


@pytest.mark.parametrize("name", DATABASE_URL_NAMES)
def test_empty_database_url_counts_as_missing(monkeypatch, name):
    message = config_error(monkeypatch, {**MINIMAL_ENV, name: ""})

    assert "missing" in line_for(message, name)


def test_direct_url_has_no_fallback_to_database_url(monkeypatch):
    # Only DATABASE_URL set: DATABASE_DIRECT_URL is never derived from it.
    env = {"APP_ENV": "local", "DATABASE_URL": VALID_DSN}

    message = config_error(monkeypatch, env)

    assert "is required but missing or empty" in line_for(
        message, "DATABASE_DIRECT_URL"
    )
    assert VALID_DSN not in message
    assert DB_PASSWORD not in message


# --- invalid ----------------------------------------------------------------


def test_invalid_app_env_lists_allowed_values(monkeypatch):
    message = config_error(monkeypatch, {**MINIMAL_ENV, "APP_ENV": "staging"})

    line = line_for(message, "APP_ENV")
    for allowed in ("local", "dev", "prod"):
        assert allowed in line


@pytest.mark.parametrize("name", DATABASE_URL_NAMES)
def test_non_postgresql_scheme_is_rejected(monkeypatch, name):
    dsn = f"mysql://{DB_USER}:{DB_PASSWORD}@db/x"

    message = config_error(monkeypatch, {**MINIMAL_ENV, name: dsn})

    assert "scheme must be 'postgresql'" in line_for(message, name)
    assert DB_PASSWORD not in message
    assert DB_USER not in message


@pytest.mark.parametrize(
    ("dsn", "reason"),
    [
        (f"postgresql://{DB_USER}:{DB_PASSWORD}@/classora", "host"),
        (f"postgresql://{DB_USER}:{DB_PASSWORD}@db.example:5432", "database name"),
        (f"postgresql://{DB_USER}:{DB_PASSWORD}@db.example:5432/", "database name"),
        (f"postgresql://{DB_USER}:{DB_PASSWORD}@[db.example/classora", "valid URL"),
    ],
)
@pytest.mark.parametrize("name", DATABASE_URL_NAMES)
def test_dsn_without_host_or_database_is_rejected(monkeypatch, dsn, reason, name):
    message = config_error(monkeypatch, {**MINIMAL_ENV, name: dsn})

    assert reason in line_for(message, name)
    assert DB_PASSWORD not in message


def test_non_boolean_cookie_secure_is_rejected(monkeypatch):
    message = config_error(
        monkeypatch, {**MINIMAL_ENV, "SESSION_COOKIE_SECURE": "maybe"}
    )

    assert "boolean" in line_for(message, "SESSION_COOKIE_SECURE")


@pytest.mark.parametrize("app_env", ["dev", "prod"])
def test_insecure_cookie_rejected_outside_local(monkeypatch, app_env):
    message = config_error(
        monkeypatch,
        {**MINIMAL_ENV, "APP_ENV": app_env, "SESSION_COOKIE_SECURE": "false"},
    )

    line = line_for(message, "SESSION_COOKIE_SECURE")
    assert "APP_ENV=local" in line


def test_insecure_cookie_accepted_in_local(monkeypatch):
    set_env(monkeypatch, {**MINIMAL_ENV, "SESSION_COOKIE_SECURE": "false"})

    assert load_settings().session_cookie_secure is False


@pytest.mark.parametrize("url", ["ftp://storage:21", "s3://bucket", "not a url"])
def test_storage_endpoint_must_be_http_or_https(monkeypatch, url):
    message = config_error(monkeypatch, {**MINIMAL_ENV, "STORAGE_ENDPOINT_URL": url})

    line_for(message, "STORAGE_ENDPOINT_URL")


@pytest.mark.parametrize(
    "present",
    [
        {"STORAGE_ACCESS_KEY_ID": "access-key-id"},
        {"STORAGE_SECRET_ACCESS_KEY": STORAGE_SECRET},
    ],
)
def test_storage_key_pair_must_be_complete(monkeypatch, present):
    message = config_error(monkeypatch, {**MINIMAL_ENV, **present})

    line = line_for(message, "STORAGE_SECRET_ACCESS_KEY")
    assert "STORAGE_ACCESS_KEY_ID" in line
    assert STORAGE_SECRET not in message


# --- multiple errors and secrets -------------------------------------------


def test_all_errors_are_reported_together(monkeypatch):
    message = config_error(
        monkeypatch,
        {
            "APP_ENV": "staging",
            "DATABASE_URL": "",
            "DATABASE_DIRECT_URL": "ftp://x/y",
            "SESSION_COOKIE_SECURE": "maybe",
            "STORAGE_ENDPOINT_URL": "ftp://x",
            "STORAGE_SECRET_ACCESS_KEY": STORAGE_SECRET,
        },
    )

    for name in (
        "APP_ENV",
        "DATABASE_URL",
        "DATABASE_DIRECT_URL",
        "SESSION_COOKIE_SECURE",
        "STORAGE_ENDPOINT_URL",
        "STORAGE_SECRET_ACCESS_KEY",
    ):
        line_for(message, name)
    assert STORAGE_SECRET not in message


def test_error_message_has_no_input_echo_or_traceback(monkeypatch):
    message = config_error(
        monkeypatch,
        {
            "APP_ENV": "prod",
            "DATABASE_URL": f"mysql://{DB_USER}:{DB_PASSWORD}@db/x",
            "DATABASE_DIRECT_URL": f"mysql://{DB_USER}:{DIRECT_PASSWORD}@db/x",
            "SESSION_COOKIE_SECURE": "false",
            "STORAGE_SECRET_ACCESS_KEY": STORAGE_SECRET,
        },
    )

    for forbidden in (
        *SECRETS,
        DB_USER,
        "input_value",
        "Traceback",
        "errors.pydantic.dev",
    ):
        assert forbidden not in message


def test_repr_and_str_mask_secrets(monkeypatch):
    set_env(monkeypatch, FULL_ENV)

    settings = load_settings()

    for rendered in (repr(settings), str(settings)):
        assert DB_PASSWORD not in rendered
        assert DIRECT_PASSWORD not in rendered
        assert STORAGE_SECRET not in rendered


# --- startup entry point ----------------------------------------------------


def run_api(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Start the API the way the container does, with exactly ``env``."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "--factory",
            "app.main:create_app",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
        ],
        cwd=API_ROOT,
        env={"PATH": os.environ.get("PATH", ""), **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_startup_exits_non_zero_without_secret_when_config_is_invalid():
    result = run_api(
        {
            "DATABASE_URL": f"mysql://{DB_USER}:{DB_PASSWORD}@db/x",
            "STORAGE_SECRET_ACCESS_KEY": STORAGE_SECRET,
        }
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Configuration error" in output
    assert "APP_ENV" in output
    assert "DATABASE_URL" in output
    for forbidden in (*SECRETS, DB_USER, "input_value", "Traceback"):
        assert forbidden not in output


def test_startup_exits_non_zero_when_required_variable_missing():
    result = run_api({"APP_ENV": "local"})

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Configuration error" in output
    assert "DATABASE_URL" in output
    assert "DATABASE_DIRECT_URL" in output
    assert "Traceback" not in output


def test_startup_exits_non_zero_when_direct_url_is_invalid():
    result = run_api(
        {
            "APP_ENV": "local",
            "DATABASE_URL": VALID_DSN,
            "DATABASE_DIRECT_URL": f"mysql://{DB_USER}:{DIRECT_PASSWORD}@db/x",
        }
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "DATABASE_DIRECT_URL: scheme must be 'postgresql'" in output
    for forbidden in (*SECRETS, DB_USER, "Traceback"):
        assert forbidden not in output


# --- .env.example drift (C3) -----------------------------------------------


def env_example_variables() -> set[str]:
    pattern = re.compile(r"^([A-Z][A-Z0-9_]*)=")
    names = set()
    for line in ENV_EXAMPLE.read_text().splitlines():
        match = pattern.match(line)
        if match:
            names.add(match.group(1))
    return names


def test_env_example_lists_every_settings_variable():
    missing = env_var_names() - env_example_variables()

    assert not missing, f".env.example is missing: {sorted(missing)}"


def test_env_example_lists_no_unknown_variables():
    unknown = env_example_variables() - env_var_names() - COMPOSE_ONLY_VARIABLES

    assert not unknown, (
        f".env.example lists variables Settings does not define: {sorted(unknown)}"
    )


def test_env_example_is_a_valid_local_configuration(monkeypatch):
    for line in ENV_EXAMPLE.read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and name in env_var_names():
            monkeypatch.setenv(name, value)

    settings = load_settings()

    assert settings.app_env == "local"


def test_settings_reads_exactly_the_documented_variables():
    assert env_var_names() == {
        "APP_ENV",
        "DATABASE_URL",
        "DATABASE_DIRECT_URL",
        "SESSION_COOKIE_SECURE",
        "STORAGE_BUCKET",
        "STORAGE_REGION",
        "STORAGE_ENDPOINT_URL",
        "STORAGE_ACCESS_KEY_ID",
        "STORAGE_SECRET_ACCESS_KEY",
    }


def test_settings_does_not_load_a_dotenv_file(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("APP_ENV=local\nDATABASE_URL=" + VALID_DSN + "\n")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigurationError):
        load_settings()


def test_settings_class_is_basesettings():
    from pydantic_settings import BaseSettings

    assert issubclass(Settings, BaseSettings)
