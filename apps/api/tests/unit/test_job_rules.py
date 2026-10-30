"""Job module rules (M1-16): driver boundary, connectors, IDs-only arguments,
environment gating and the worker's start-up checks. No database needed."""

import ast
import inspect
import logging
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from procrastinate import App, JobContext, PsycopgConnector
from pydantic import SecretStr

from app import worker
from app.core import jobs
from app.core.config import Settings
from app.main import create_app
from tests.authz.csrf import origin_for

API_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = API_ROOT / "app"

POOLED_PASSWORD = "Pooled-S3cret-pw-41"
DIRECT_PASSWORD = "Direct-S3cret-pw-42"
POOLED_DSN = f"postgresql://jobs_user:{POOLED_PASSWORD}@pgbouncer:6432/classora"
DIRECT_DSN = f"postgresql://jobs_user:{DIRECT_PASSWORD}@db:5432/classora"

FORBIDDEN_DRIVER_MODULES = {"psycopg", "psycopg_pool", "asyncpg"}


def settings(app_env: str = "local") -> Settings:
    return Settings(
        app_env=app_env,  # type: ignore[arg-type]
        app_origin=origin_for(app_env),
        database_url=SecretStr(POOLED_DSN),
        database_direct_url=SecretStr(DIRECT_DSN),
    )


def pool_args(connector: PsycopgConnector) -> dict:
    return connector._pool_args


# --- A3 driver boundary -------------------------------------------------------


def driver_imports(root: Path) -> list[str]:
    """``path:line module`` (relative to ``root``) for every psycopg/asyncpg
    import under ``root``."""
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                if module.split(".")[0] in FORBIDDEN_DRIVER_MODULES:
                    found.append(f"{path.relative_to(root)}:{node.lineno} {module}")
    return found


def test_app_never_imports_a_database_driver_directly():
    # ADR-0003: psycopg is Procrastinate's driver only; the ORM uses asyncpg
    # through SQLAlchemy. Connection options are plain kwargs in app/core/jobs.py.
    assert driver_imports(APP_ROOT) == []


def test_import_guard_detects_driver_imports(tmp_path):
    (tmp_path / "bad.py").write_text(
        "import psycopg\nfrom psycopg_pool import AsyncConnectionPool\n"
        "import asyncpg.pool\nimport psycopg2\n"
    )
    assert driver_imports(tmp_path) == [
        "bad.py:1 psycopg",
        "bad.py:2 psycopg_pool",
        "bad.py:3 asyncpg.pool",
    ]


# --- C1/C2 connectors -----------------------------------------------------------


def test_api_connector_uses_the_pooled_url_without_prepared_statements():
    args = pool_args(jobs.api_connector(settings()))

    assert args["conninfo"] == POOLED_DSN
    assert args["kwargs"]["prepare_threshold"] is None
    assert args["kwargs"]["application_name"] == "classora-api-jobs"
    assert args["min_size"] <= 1
    assert args["max_size"] <= 2


def test_worker_connector_uses_the_direct_url():
    args = pool_args(jobs.worker_connector(settings()))

    assert args["conninfo"] == DIRECT_DSN
    assert args["kwargs"]["application_name"] == "classora-worker"
    assert args["min_size"] <= 1
    assert args["max_size"] <= 3


def test_worker_healthcheck_connector_uses_the_direct_url():
    args = pool_args(jobs.worker_healthcheck_connector(settings()))

    assert args["conninfo"] == DIRECT_DSN
    assert args["max_size"] == 1


def test_worker_engine_uses_the_direct_url():
    engine = jobs.create_worker_engine(settings())

    assert engine.url.host == "db"
    assert engine.url.port == 5432
    assert engine.url.drivername == "postgresql+asyncpg"


def test_worker_job_app_never_reads_the_pooled_url(monkeypatch):
    # Only DATABASE_DIRECT_URL is valid; the pooled one would fail if used.
    poisoned = settings().model_copy(
        update={"database_url": SecretStr("postgresql://x:y@poisoned:1/never")}
    )
    app = jobs.create_worker_job_app(poisoned)

    assert pool_args(app.connector)["conninfo"] == DIRECT_DSN  # type: ignore[arg-type]


def test_connection_urls_never_appear_in_repr_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    s = settings()
    objects = [
        jobs.api_connector(s),
        jobs.worker_connector(s),
        jobs.create_api_job_app(s),
        jobs.create_worker_job_app(s),
        jobs.create_worker_engine(s),
        s,
    ]
    text = " ".join(str(o) + repr(o) for o in objects) + caplog.text

    for secret in (POOLED_PASSWORD, DIRECT_PASSWORD, POOLED_DSN, DIRECT_DSN):
        assert secret not in text


# --- C5 IDs-only task arguments ------------------------------------------------


def task_argument_problems(app: App) -> list[str]:
    """Every task parameter must be an ID type (ISSUE-J3); the first parameter
    of a ``pass_context`` task is the ``JobContext``."""
    problems = []
    for task_name, parameters in jobs.task_parameter_types(app).items():
        task = app.tasks[task_name]
        for index, (name, annotation) in enumerate(parameters.items()):
            if task.pass_context and index == 0:
                if annotation is not JobContext:
                    problems.append(f"{task_name}.{name}: context must be JobContext")
                continue
            if annotation is inspect.Parameter.empty:
                problems.append(f"{task_name}.{name}: unannotated")
            elif annotation not in jobs.ALLOWED_ARGUMENT_TYPES:
                problems.append(f"{task_name}.{name}: {annotation!r} is not allowed")
    return problems


@pytest.mark.parametrize("app_env", ["local", "dev", "prod"])
def test_every_registered_task_takes_ids_only(app_env):
    app = jobs.create_job_app(jobs.worker_connector(settings()), app_env=app_env)

    assert task_argument_problems(app) == []


def test_argument_guard_rejects_free_text_and_unannotated_parameters():
    app = jobs.create_job_app(jobs.worker_connector(settings()), app_env="prod")

    @app.task(name="test:greet")
    async def greet(name: str) -> None: ...

    @app.task(name="test:untyped")
    async def untyped(record_id) -> None: ...  # type: ignore[no-untyped-def]

    @app.task(name="test:ok")
    async def ok(record_id: uuid.UUID, count: int) -> None: ...

    problems = task_argument_problems(app)
    assert problems == [
        "test:greet.name: <class 'str'> is not allowed",
        "test:untyped.record_id: unannotated",
    ]


def test_allowed_argument_types_are_ids():
    assert jobs.ALLOWED_ARGUMENT_TYPES == {uuid.UUID, int}


@pytest.mark.anyio
async def test_ids_only_turns_json_strings_back_into_uuids():
    received = []

    @jobs.ids_only
    async def task(record_id: uuid.UUID, count: int) -> None:
        received.append((record_id, count))

    value = uuid.uuid7()
    await task(record_id=str(value), count=3)
    await task(record_id=value, count=4)

    assert received == [(value, 3), (value, 4)]


# --- D4/D5 environment gating ----------------------------------------------------

DEMO_TASKS = {"demo:succeed", "demo:fail", "demo:periodic"}
# Registered in every environment (#25, #27).
MODULE_TASKS = {
    "identity:purge_auth_sessions",
    "idempotency:purge_keys",
    "ratelimit:purge_counters",
    "scheduling:expire_offers",
    "scheduling:materialize_series",
    "academics:create_progress_report_placeholders",
    "billing:post_session_charges",
    "billing:post_enrollment_charges",
    "billing:post_tuition_charges",
    "billing:expire_packages",
    "billing:send_charge_reminders",
    "billing:reconcile_session_charges",
}


def app_tasks(app: App) -> set[str]:
    # Procrastinate's built-in tasks are registered under "builtin:..." names.
    return set(jobs.task_parameter_types(app))


def periodic_tasks(app: App) -> set[str]:
    return {task.task.name for task in app.periodic_registry.periodic_tasks.values()}


@pytest.mark.parametrize("app_env", ["local", "dev"])
def test_demo_tasks_and_periodic_demo_exist_in_local_and_dev(app_env):
    app = jobs.create_worker_job_app(settings(app_env))

    assert app_tasks(app) == DEMO_TASKS | MODULE_TASKS
    assert periodic_tasks(app) == {"demo:periodic"} | MODULE_TASKS
    [periodic] = [
        p
        for p in app.periodic_registry.periodic_tasks.values()
        if p.task.name == "demo:periodic"
    ]
    assert periodic.cron == "* * * * *"
    assert periodic.periodic_id == "demo"
    assert periodic.configure_kwargs["queueing_lock"] == "demo-periodic"


def test_prod_registers_no_demo_task_and_schedules_only_module_tasks():
    app = jobs.create_worker_job_app(settings("prod"))

    assert app_tasks(app) == MODULE_TASKS
    assert periodic_tasks(app) == MODULE_TASKS


def test_the_series_materialization_runs_nightly_at_08_utc():
    # #53 F4: Procrastinate evaluates the cron on UTC timestamps.
    app = jobs.create_worker_job_app(settings("prod"))

    [periodic] = [
        p
        for p in app.periodic_registry.periodic_tasks.values()
        if p.task.name == "scheduling:materialize_series"
    ]
    assert periodic.cron == "0 8 * * *"
    assert periodic.periodic_id == "scheduling-materialize-series"
    assert periodic.configure_kwargs["queueing_lock"] == "scheduling-materialize-series"
    assert jobs.task_parameter_types(app)["scheduling:materialize_series"] == {
        "timestamp": int
    }


def test_the_progress_report_placeholders_run_daily_at_07_utc():
    # #316 D1: one job a day (01:00 in Mexico City); correctness depends only
    # on the center-local today of the run.
    app = jobs.create_worker_job_app(settings("prod"))

    [periodic] = [
        p
        for p in app.periodic_registry.periodic_tasks.values()
        if p.task.name == "academics:create_progress_report_placeholders"
    ]
    assert periodic.cron == "0 7 * * *"
    assert periodic.periodic_id == "academics-progress-report-placeholders"
    assert (
        periodic.configure_kwargs["queueing_lock"]
        == "academics-progress-report-placeholders"
    )
    assert (
        app.tasks["academics:create_progress_report_placeholders"].queueing_lock
        == "academics-progress-report-placeholders"
    )
    assert jobs.task_parameter_types(app)[
        "academics:create_progress_report_placeholders"
    ] == {"timestamp": int}


def test_the_session_charges_run_every_15_minutes():
    # #91 C1: one queued job at a time; the settings PATCH shares the lock.
    app = jobs.create_worker_job_app(settings("prod"))

    [periodic] = [
        p
        for p in app.periodic_registry.periodic_tasks.values()
        if p.task.name == "billing:post_session_charges"
    ]
    assert periodic.cron == "*/15 * * * *"
    assert periodic.periodic_id == "billing-post-session-charges"
    assert periodic.configure_kwargs["queueing_lock"] == "billing-post-session-charges"
    assert app.tasks["billing:post_session_charges"].queueing_lock == (
        jobs.SESSION_CHARGES_QUEUEING_LOCK
    )
    assert jobs.SESSION_CHARGES_TASK == "billing:post_session_charges"
    assert jobs.task_parameter_types(app)["billing:post_session_charges"] == {
        "timestamp": int
    }


def test_the_enrollment_charges_run_every_15_minutes():
    # #92 D1: one queued job at a time; the settings PATCH shares the lock.
    app = jobs.create_worker_job_app(settings("prod"))

    [periodic] = [
        p
        for p in app.periodic_registry.periodic_tasks.values()
        if p.task.name == "billing:post_enrollment_charges"
    ]
    assert periodic.cron == "*/15 * * * *"
    assert periodic.periodic_id == "billing-post-enrollment-charges"
    assert periodic.configure_kwargs["queueing_lock"] == (
        "billing-post-enrollment-charges"
    )
    assert app.tasks["billing:post_enrollment_charges"].queueing_lock == (
        jobs.ENROLLMENT_CHARGES_QUEUEING_LOCK
    )
    assert jobs.ENROLLMENT_CHARGES_TASK == "billing:post_enrollment_charges"
    assert jobs.task_parameter_types(app)["billing:post_enrollment_charges"] == {
        "timestamp": int
    }


def test_the_tuition_charges_run_daily_at_0730_utc():
    # #93 E1 (K5): one queued job at a time; the plan writes and the settings
    # PATCH share the lock.
    app = jobs.create_worker_job_app(settings("prod"))

    [periodic] = [
        p
        for p in app.periodic_registry.periodic_tasks.values()
        if p.task.name == "billing:post_tuition_charges"
    ]
    assert periodic.cron == "30 7 * * *"
    assert periodic.periodic_id == "billing-post-tuition-charges"
    assert periodic.configure_kwargs["queueing_lock"] == (
        "billing-post-tuition-charges"
    )
    assert app.tasks["billing:post_tuition_charges"].queueing_lock == (
        jobs.TUITION_CHARGES_QUEUEING_LOCK
    )
    assert jobs.TUITION_CHARGES_TASK == "billing:post_tuition_charges"
    assert jobs.task_parameter_types(app)["billing:post_tuition_charges"] == {
        "timestamp": int
    }


def test_the_package_expiry_runs_daily_at_0740_utc():
    # #94 F1: after the 07:30 tuition charges; one queued job at a time.
    app = jobs.create_worker_job_app(settings("prod"))

    [periodic] = [
        p
        for p in app.periodic_registry.periodic_tasks.values()
        if p.task.name == "billing:expire_packages"
    ]
    assert periodic.cron == "40 7 * * *"
    assert periodic.periodic_id == "billing-expire-packages"
    assert periodic.configure_kwargs["queueing_lock"] == "billing-expire-packages"
    assert app.tasks["billing:expire_packages"].queueing_lock == (
        jobs.PACKAGE_EXPIRY_QUEUEING_LOCK
    )
    assert jobs.PACKAGE_EXPIRY_TASK == "billing:expire_packages"
    assert jobs.task_parameter_types(app)["billing:expire_packages"] == {
        "timestamp": int
    }


def test_the_charge_reminders_run_daily_at_1500_utc():
    # #97 F2 (N4 (a)): 09:00 in Mexico City; one queued job at a time.
    app = jobs.create_worker_job_app(settings("prod"))

    [periodic] = [
        p
        for p in app.periodic_registry.periodic_tasks.values()
        if p.task.name == "billing:send_charge_reminders"
    ]
    assert periodic.cron == "0 15 * * *"
    assert periodic.periodic_id == "billing-send-charge-reminders"
    assert periodic.configure_kwargs["queueing_lock"] == (
        "billing-send-charge-reminders"
    )
    assert app.tasks["billing:send_charge_reminders"].queueing_lock == (
        jobs.CHARGE_REMINDERS_QUEUEING_LOCK
    )
    assert jobs.CHARGE_REMINDERS_TASK == "billing:send_charge_reminders"
    assert jobs.task_parameter_types(app)["billing:send_charge_reminders"] == {
        "timestamp": int
    }


def test_the_schedule_reconciliation_runs_every_15_minutes():
    # #99 D1, D9: one queued job at a time; the routes that change sessions,
    # the settings PATCH and the materialization share the lock.
    app = jobs.create_worker_job_app(settings("prod"))

    [periodic] = [
        p
        for p in app.periodic_registry.periodic_tasks.values()
        if p.task.name == "billing:reconcile_session_charges"
    ]
    assert periodic.cron == "*/15 * * * *"
    assert periodic.periodic_id == "billing-reconcile-session-charges"
    assert periodic.configure_kwargs["queueing_lock"] == (
        "billing-reconcile-session-charges"
    )
    assert app.tasks["billing:reconcile_session_charges"].queueing_lock == (
        jobs.SCHEDULE_RECONCILIATION_QUEUEING_LOCK
    )
    assert jobs.SCHEDULE_RECONCILIATION_TASK == "billing:reconcile_session_charges"
    assert jobs.task_parameter_types(app)["billing:reconcile_session_charges"] == {
        "timestamp": int
    }


def test_demo_tasks_use_fixed_queueing_locks_and_a_retry_policy():
    app = jobs.create_worker_job_app(settings("local"))

    assert app.tasks["demo:succeed"].queueing_lock == "demo-succeed"
    assert app.tasks["demo:fail"].queueing_lock == "demo-fail"
    retry = app.tasks["demo:fail"].retry_strategy
    assert retry is not None
    # 3 runs in all: the first one plus 2 retries, 2 s apart.
    assert (retry.max_attempts, retry.wait) == (2, 2)


def make_client(monkeypatch: pytest.MonkeyPatch, app_env: str) -> TestClient:
    placeholder = "postgresql://placeholder:placeholder@127.0.0.1:1/placeholder"
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("APP_ORIGIN", origin_for(app_env))
    monkeypatch.setenv("DATABASE_URL", placeholder)
    monkeypatch.setenv("DATABASE_DIRECT_URL", placeholder)
    return TestClient(
        create_app(),
        raise_server_exceptions=False,
        headers={"Origin": origin_for(app_env)},
    )


@pytest.mark.parametrize("app_env", ["local", "dev"])
def test_demo_route_exists_in_local_and_dev(monkeypatch, app_env):
    with make_client(monkeypatch, app_env) as client:
        paths = client.get("/openapi.json").json()["paths"]
        invalid = client.post("/api/v1/dev/demo-jobs", json={"outcome": "maybe"})
        live = client.get("/health/live")

    assert "/api/v1/dev/demo-jobs" in paths
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "validation_error"
    # The job pool opens without connecting: the API starts with no database.
    assert live.status_code == 200


def test_demo_route_is_absent_in_prod(monkeypatch):
    with make_client(monkeypatch, "prod") as client:
        paths = client.get("/openapi.json").json()["paths"]
        response = client.post("/api/v1/dev/demo-jobs")

    assert "/api/v1/dev/demo-jobs" not in paths
    assert response.status_code == 404


# --- C4 worker start-up and options -------------------------------------------------


@pytest.mark.parametrize("value", ["0", "31", "abc", "-1", "2.5"])
def test_invalid_shutdown_timeout_stops_the_worker(monkeypatch, capsys, value):
    placeholder = f"postgresql://u:{DIRECT_PASSWORD}@127.0.0.1:1/x"
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("APP_ORIGIN", origin_for("local"))
    monkeypatch.setenv("DATABASE_URL", placeholder)
    monkeypatch.setenv("DATABASE_DIRECT_URL", placeholder)
    monkeypatch.setenv("WORKER_SHUTDOWN_TIMEOUT_SECONDS", value)

    assert worker.main([]) == 1

    output = capsys.readouterr()
    text = output.out + output.err
    assert text.startswith("Configuration error")
    assert "WORKER_SHUTDOWN_TIMEOUT_SECONDS" in text
    assert f"input_value='{value}'" not in text
    assert f"'{value}'" not in text
    assert DIRECT_PASSWORD not in text
    assert "Traceback" not in text


@pytest.mark.parametrize(("value", "expected"), [(None, 20), ("1", 1), ("30", 30)])
def test_shutdown_timeout_default_and_bounds(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("WORKER_SHUTDOWN_TIMEOUT_SECONDS", value)
    s = Settings(
        app_env="local",
        app_origin=origin_for("local"),
        database_url=SecretStr(POOLED_DSN),
        database_direct_url=SecretStr(DIRECT_DSN),
    )

    assert s.worker_shutdown_timeout_seconds == expected
    assert worker.worker_options(s)["shutdown_graceful_timeout"] == float(expected)


def test_worker_runs_one_job_at_a_time_and_handles_its_own_signals():
    options = worker.worker_options(settings())

    assert options["concurrency"] == 1
    assert options["name"] == "classora-worker"
    assert options["install_signal_handlers"] is False


def test_worker_rejects_unknown_arguments(monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("APP_ORIGIN", origin_for("local"))
    monkeypatch.setenv("DATABASE_URL", POOLED_DSN)
    monkeypatch.setenv("DATABASE_DIRECT_URL", DIRECT_DSN)

    assert worker.main(["--apply-schema"]) == 2
    assert "usage" in capsys.readouterr().err


def test_healthcheck_fails_fast_when_postgresql_is_unreachable(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("APP_ORIGIN", origin_for("local"))
    unreachable = "postgresql://u:p@127.0.0.1:1/x"
    monkeypatch.setenv("DATABASE_URL", unreachable)
    monkeypatch.setenv("DATABASE_DIRECT_URL", unreachable)

    assert worker.main(["--healthcheck"]) == 1


# --- B5 nothing in app/ applies the schema --------------------------------------------


def test_app_never_applies_the_procrastinate_schema():
    offenders = [
        str(path.relative_to(API_ROOT))
        for path in APP_ROOT.rglob("*.py")
        if "apply_schema" in path.read_text(encoding="utf-8")
        or "schema --apply" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
