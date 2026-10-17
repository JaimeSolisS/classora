"""``alembic upgrade head`` failure paths that need no database (D4)."""

import os
import subprocess
import sys
from pathlib import Path

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
        }
    )

    assert result.returncode != 0
    assert DB_PASSWORD not in result.stdout + result.stderr
