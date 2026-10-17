"""``alembic upgrade head`` paths that need no database (M1-07 D4, M1-08 C2/D2)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

API_ROOT = Path(__file__).resolve().parents[2]
DB_USER = "leakcheck_user"
DB_PASSWORD = "not-a-real-secret-pw"


def run_alembic(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run Alembic the way ``make migrate`` does, with exactly ``env``."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_ROOT,
        env={"PATH": os.environ.get("PATH", ""), **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_configuration_error_prints_the_short_message():
    result = run_alembic({"DATABASE_URL": f"mysql://{DB_USER}:{DB_PASSWORD}@db/x"})

    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "Configuration error" in output
    assert "APP_ENV" in output
    for forbidden in (DB_PASSWORD, DB_USER, "Traceback"):
        assert forbidden not in output


def test_unreachable_database_fails_without_leaking_the_password():
    result = run_alembic(
        {
            "APP_ENV": "local",
            "DATABASE_URL": f"postgresql://{DB_USER}:{DB_PASSWORD}@127.0.0.1:1/db",
            "DATABASE_DIRECT_URL": (
                f"postgresql://{DB_USER}:{DB_PASSWORD}@127.0.0.1:1/db"
            ),
        }
    )

    assert result.returncode != 0
    assert DB_PASSWORD not in result.stdout + result.stderr


def test_missing_direct_url_is_a_configuration_error():
    result = run_alembic(
        {
            "APP_ENV": "local",
            "DATABASE_URL": f"postgresql://{DB_USER}:{DB_PASSWORD}@127.0.0.1:1/db",
        }
    )

    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "DATABASE_DIRECT_URL: is required but missing or empty" in output
    for forbidden in (DB_PASSWORD, DB_USER, "Traceback"):
        assert forbidden not in output


class _Stop(Exception):
    pass


def test_env_builds_its_engine_from_the_direct_url(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:pooled-pw@pgbouncer:6432/app")
    monkeypatch.setenv("DATABASE_DIRECT_URL", "postgresql://u:direct-pw@db:5432/app")
    urls = []

    def fake_create_direct_engine(url, **kwargs):
        urls.append(url)
        raise _Stop

    # env.py imports create_direct_engine from app.core.db each time it runs.
    monkeypatch.setattr("app.core.db.create_direct_engine", fake_create_direct_engine)
    config = Config(str(API_ROOT / "alembic.ini"))
    config.attributes["configure_logging"] = False

    with pytest.raises(_Stop):
        command.upgrade(config, "head")

    assert len(urls) == 1
    assert urls[0].startswith("postgresql+asyncpg://u:direct-pw@db:5432/app")
