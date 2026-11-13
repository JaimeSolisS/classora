"""The simulated operations command without a database (#543 A1-A3): the
make targets, the local-only guard, the arguments and the help."""

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.authz.csrf import origin_for
from tests.factories.simulation import __main__ as command
from tests.factories.simulation import plan as simulation_plan
from tests.factories.simulation import timeline

API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = API_ROOT.parents[1]
CLOSED_PORT_DSN = "postgresql://x:y@127.0.0.1:1/z"


class EngineRequested(Exception):
    pass


def failing_engine_factory(settings: Any) -> Any:
    raise EngineRequested()


@pytest.fixture
def env(monkeypatch):
    def _set(app_env: str) -> None:
        monkeypatch.setenv("APP_ENV", app_env)
        monkeypatch.setenv("APP_ORIGIN", origin_for("prod"))
        monkeypatch.setenv("DATABASE_URL", CLOSED_PORT_DSN)
        monkeypatch.setenv("DATABASE_DIRECT_URL", CLOSED_PORT_DSN)

    return _set


@pytest.fixture
def no_faker(monkeypatch):
    """Fails the test if the command builds the plan or calls Faker."""

    def _fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Faker or the plan was used")

    monkeypatch.setattr(simulation_plan.faker, "Faker", _fail)
    monkeypatch.setattr(timeline, "plan_simulation", _fail)
    monkeypatch.setattr(timeline, "make_faker", _fail)


def _target(lines: list[str], target: str) -> tuple[str, str]:
    """The first recipe line of ``target`` and the ``##`` help line above it."""
    index = lines.index(f"{target}:")
    # A `##` help comment right above the target, like the other targets.
    above = [line for line in lines[:index] if line.startswith("##")][-1]
    assert f" {target} " in f" {lines[0]} "  # in .PHONY
    return lines[index + 1], above


@pytest.mark.parametrize("language", ["es", "en"])
def test_make_targets_run_the_command_per_language(language):
    lines = (REPO_ROOT / "Makefile").read_text().splitlines()
    name = {"es": "spanish", "en": "english"}[language]

    # #672 D4: the grader path runs it in the Compose `devtools` service.
    recipe, above = _target(lines, f"seed-data-{name}")
    assert recipe == (
        "\tdocker compose run --rm --build devtools python -m"
        f" tests.factories.simulation --language {language}"
    )
    assert "APP_ENV=local only" in above

    # #672 D5: the host recipe is unchanged, under its -native name.
    recipe, above = _target(lines, f"seed-data-{name}-native")
    assert recipe == (
        "\tcd apps/api && uv run --env-file ../../.env python -m"
        f" tests.factories.simulation --language {language}"
    )
    assert "APP_ENV=local only" in above


def test_the_devtools_service_runs_the_api_test_stage_as_the_owner():
    # #672 D4: migrate's settings (the owner, directly to db), in the image
    # stage that has the dev dependencies and tests/; local Compose only.
    compose = (REPO_ROOT / "compose.yaml").read_text()
    block = compose[compose.index("\n  devtools:\n") :]
    block = block[: block.index("\n\n")]
    assert "      file: infrastructure/docker/compose.app.yaml" in block
    assert "      service: migrate" in block
    assert "      target: test" in block
    assert "condition: service_healthy" in block
    app_compose = REPO_ROOT / "infrastructure" / "docker" / "compose.app.yaml"
    assert "devtools" not in app_compose.read_text()


@pytest.mark.parametrize(
    "app_env", ["dev", "test", "staging", "production", "prod", "LOCAL", "anything"]
)
def test_every_app_env_but_local_is_refused_before_any_engine(
    env, no_faker, capsys, app_env
):
    env(app_env)

    code = command.main(["--language", "es"], engine_factory=failing_engine_factory)

    assert code == 2
    captured = capsys.readouterr()
    assert captured.err.strip() == (
        f"Refusing to generate simulated data: APP_ENV={app_env} (local only)"
    )
    assert captured.out == ""


def test_local_passes_the_guard(env, no_faker):
    env("local")

    # The command goes on to create its engine, which this factory refuses.
    with pytest.raises(EngineRequested):
        command.main(["--language", "en"], engine_factory=failing_engine_factory)


def test_refusal_is_local_only():
    assert command.refusal("local") is None
    assert command.refusal("dev") == (
        "Refusing to generate simulated data: APP_ENV=dev (local only)"
    )


@pytest.mark.parametrize(
    "argv", [[], ["--language", "fr"], ["--language"], ["--language", "ES"]]
)
def test_a_missing_or_invalid_language_is_an_argparse_error(
    env, no_faker, capsys, argv
):
    env("local")

    with pytest.raises(SystemExit) as exit_info:
        command.main(argv, engine_factory=failing_engine_factory)

    assert exit_info.value.code == 2
    assert "--language" in capsys.readouterr().err


def test_help_names_the_languages_local_only_and_a_fresh_database(capsys):
    with pytest.raises(SystemExit) as exit_info:
        command.main(["--help"])

    assert exit_info.value.code == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "{es,en}" in out
    assert "Spanish (es)" in out and "English (en)" in out
    assert "Local only (APP_ENV=local)" in out
    assert "freshly migrated database" in out


def test_configuration_error_exits_1(no_faker, monkeypatch, capsys):
    monkeypatch.delenv("APP_ENV", raising=False)

    code = command.main(["--language", "es"], engine_factory=failing_engine_factory)

    assert code == 1
    err = capsys.readouterr().err
    assert err.startswith("Configuration error: the API cannot start.")
    assert "Traceback" not in err


def test_dev_refusal_as_a_process_returns_immediately():
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"APP_ENV", "DATABASE_URL", "DATABASE_DIRECT_URL"}
    }
    environment |= {
        "APP_ENV": "dev",
        "APP_ORIGIN": origin_for("dev"),
        "DATABASE_URL": CLOSED_PORT_DSN,
        "DATABASE_DIRECT_URL": CLOSED_PORT_DSN,
    }
    started = time.monotonic()
    completed = subprocess.run(  # noqa: S603 - a fixed argv, no shell
        [sys.executable, "-m", "tests.factories.simulation", "--language", "es"],
        cwd=API_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 2
    assert completed.stderr.strip() == (
        "Refusing to generate simulated data: APP_ENV=dev (local only)"
    )
    assert time.monotonic() - started < 15


def test_the_package_name_does_not_match_the_demo_recipe_pin():
    # test_demo_guard.py requires that no recipe line contains this string.
    makefile = (REPO_ROOT / "Makefile").read_text()
    recipes = [
        line for line in makefile.splitlines() if "tests.factories.simulation" in line
    ]
    assert len(recipes) == 4  # seed-data-{spanish,english}[-native]
    assert all("python -m tests.factories.demo" not in line for line in recipes)


def test_only_the_owner_bootstrap_inserts_a_domain_row_directly():
    # D9: every other write goes through a service.
    package = API_ROOT / "tests" / "factories" / "simulation"
    direct = {
        path.name: path.read_text().count("session.add(")
        for path in package.glob("*.py")
        if "session.add(" in path.read_text()
    }
    assert direct == {"people.py": 2}  # the Owner's users and user_roles rows
    people = (package / "people.py").read_text()
    bootstrap = people[people.index("async def _bootstrap_owner") :]
    bootstrap = bootstrap[: bootstrap.index("\nasync def ", 1)]
    assert bootstrap.count("session.add(") == 2
