"""Demo-data command without a database (M1-17 C5): the production guard,
and the payroll, M7-demo-b and M8-demo generators' skips without a session
(M7-demo #499 E5, M7-demo-b #501 F5, M8-demo #539 A2/A3)."""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import factory
import pytest

from tests.authz.csrf import origin_for
from tests.factories import demo
from tests.factories.base import GeneratorSkipped
from tests.factories.demo_admin import (
    DemoAdminGenerator,
    DemoDeletedGenerator,
    DemoImportsGenerator,
    DemoReceiptMessageGenerator,
    DemoSetupsGenerator,
)
from tests.factories.demo_billing import WITHOUT_BILLING
from tests.factories.demo_finance import (
    DemoFinanceExpensesGenerator,
    DemoFinanceSetupGenerator,
    DemoFinanceTemplatesGenerator,
    DemoPastCohortFeeGenerator,
)
from tests.factories.demo_payroll import (
    DemoPayrollCorrectionsGenerator,
    DemoPayrollPeriodsGenerator,
    DemoPayrollSetupGenerator,
)

API_ROOT = Path(__file__).resolve().parents[2]
CLOSED_PORT_DSN = "postgresql://x:y@127.0.0.1:1/z"


class EngineRequested(Exception):
    pass


def failing_engine_factory(settings: Any) -> Any:
    raise EngineRequested()


@pytest.fixture
def env(monkeypatch):
    def _set(app_env: str) -> None:
        monkeypatch.setenv("APP_ENV", app_env)
        monkeypatch.setenv("APP_ORIGIN", origin_for(app_env))
        monkeypatch.setenv("DATABASE_URL", CLOSED_PORT_DSN)
        monkeypatch.setenv("DATABASE_DIRECT_URL", CLOSED_PORT_DSN)

    return _set


@pytest.fixture
def no_randomness(monkeypatch):
    """Fails the test if the command seeds or calls Faker."""

    def _fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Faker or the random generator was used")

    monkeypatch.setattr(demo, "reseed", _fail)
    monkeypatch.setattr(factory.Faker, "_get_faker", classmethod(_fail))


def test_prod_is_refused_before_any_connection(env, no_randomness, capsys):
    env("prod")

    assert demo.main([], engine_factory=failing_engine_factory) == 2

    captured = capsys.readouterr()
    assert captured.err.strip() == "Refusing to generate demo data: APP_ENV=prod"
    assert captured.out == ""


@pytest.mark.parametrize("app_env", ["local", "dev"])
def test_local_and_dev_pass_the_guard(env, app_env):
    env(app_env)

    # The command goes on to create its engine, which this factory refuses.
    with pytest.raises(EngineRequested):
        demo.main([], engine_factory=failing_engine_factory)


@pytest.mark.parametrize("app_env", ["prod", "staging", "test", "", "LOCAL"])
def test_guard_is_an_allow_list(app_env):
    assert demo.refusal(app_env) == f"Refusing to generate demo data: APP_ENV={app_env}"


@pytest.mark.parametrize("app_env", ["local", "dev"])
def test_guard_allows_local_and_dev(app_env):
    assert demo.refusal(app_env) is None


def test_configuration_error_exits_1(no_randomness, capsys):
    assert demo.main([], engine_factory=failing_engine_factory) == 1

    err = capsys.readouterr().err
    assert err.startswith("Configuration error: the API cannot start.")
    assert "Traceback" not in err


def test_prod_refusal_as_a_process_returns_immediately():
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"APP_ENV", "DATABASE_URL", "DATABASE_DIRECT_URL"}
    }
    environment |= {
        "APP_ENV": "prod",
        "APP_ORIGIN": origin_for("prod"),
        "DATABASE_URL": CLOSED_PORT_DSN,
        "DATABASE_DIRECT_URL": CLOSED_PORT_DSN,
    }
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-m", "tests.factories.demo"],
        cwd=API_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 2
    assert completed.stderr.strip() == "Refusing to generate demo data: APP_ENV=prod"
    assert "Traceback" not in completed.stderr
    assert time.monotonic() - started < 10


def test_the_billing_flag_is_documented_for_e2e(capsys):
    # M6-demo A5, #499 A4, #539 E1: --without-billing names the E2E run that
    # passes it and the billing, payroll, expense and M8 data it leaves out.
    with pytest.raises(SystemExit) as exit_info:
        demo.main(["--help"])

    assert exit_info.value.code == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "--without-billing" in out
    assert "e2e/scripts/run.sh" in out
    assert (
        "without the billing, payroll, expense and M8 administration demo data"
        " (E2E;" in out
    )
    assert "E2E database keeps them off" in out


@pytest.mark.parametrize("app_env", ["local", "dev"])
def test_the_billing_flag_passes_the_guard(env, app_env):
    env(app_env)

    with pytest.raises(EngineRequested):
        demo.main(["--without-billing"], engine_factory=failing_engine_factory)


def test_make_seed_demo_does_not_skip_billing_and_e2e_does():
    # M6-demo A5: only e2e/scripts/run.sh passes the flag.
    makefile = (API_ROOT.parents[1] / "Makefile").read_text()
    [recipe] = [
        line
        for line in makefile.splitlines()
        if "python -m tests.factories.demo" in line
    ]
    assert "--without-billing" not in recipe
    run_sh = (API_ROOT.parents[1] / "e2e" / "scripts" / "run.sh").read_text()
    [step] = [
        line for line in run_sh.splitlines() if "python -m tests.factories.demo" in line
    ]
    assert "--without-billing" in step


class UntouchedSession:
    """Fails the test if the generator uses the session."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the session was used ({name})")


PAYROLL_GENERATORS = [
    DemoPayrollSetupGenerator,
    DemoPayrollPeriodsGenerator,
    DemoPayrollCorrectionsGenerator,
    # M7-demo-b #501 F5: the cohort fee and the three finance generators.
    DemoPastCohortFeeGenerator,
    DemoFinanceSetupGenerator,
    DemoFinanceTemplatesGenerator,
    DemoFinanceExpensesGenerator,
    # M8-demo #539 A2, A3: the five M8 generators.
    DemoReceiptMessageGenerator,
    DemoAdminGenerator,
    DemoImportsGenerator,
    DemoSetupsGenerator,
    DemoDeletedGenerator,
]


@pytest.mark.parametrize("app_env", ["dev", "prod", "staging"])
@pytest.mark.parametrize("generator", PAYROLL_GENERATORS, ids=lambda g: g.name)
def test_a_payroll_generator_writes_nothing_outside_local(generator, app_env):
    # #499 E5: skipped without touching the session.
    result = asyncio.run(
        generator(app_env=lambda: app_env).generate(UntouchedSession())  # type: ignore[arg-type]
    )

    assert result == GeneratorSkipped(f"APP_ENV={app_env}; local only")


@pytest.mark.parametrize("generator", PAYROLL_GENERATORS, ids=lambda g: g.name)
def test_a_payroll_generator_writes_nothing_under_the_flag(generator):
    # #499 E5, A4: --without-billing, without touching the session.
    result = asyncio.run(
        generator(app_env=lambda: "local", skipped=lambda: True).generate(
            UntouchedSession()  # type: ignore[arg-type]
        )
    )

    assert result == GeneratorSkipped(WITHOUT_BILLING)
