"""Testcontainers PostgreSQL for integration tests.

Strategy: one PostgreSQL container per pytest session, started lazily the
first time an integration test requests it and stopped at session end. It is
published on a random host port chosen by Testcontainers, so it never clashes
with the Compose ``db`` and never touches its volume. Unit-only runs start no
container.

Per-test isolation: a test that writes data or runs migrations asks for
``fresh_database_url`` (an empty database created in the session container
just for that test and dropped with ``FORCE`` afterwards) or ``tables_url``
(the same, with the test-only tables from ``tests/integration/models.py``
created), ``head_url`` (the same, migrated to the Alembic head) or
``seeded_url`` (at head with every reference seed applied). Tests therefore
share no data and can run in any order. Only the
read-only readiness tests use the container's default database.

PgBouncer (M1-08): the tests that need it ask for ``pgbouncer_url``. That
builds the image from ``infrastructure/docker/pgbouncer`` and starts it once
per session next to the PostgreSQL container (reached as ``db``, like in
Compose), in transaction mode with a small server pool
(``PGBOUNCER_POOL_SIZE``). Other integration tests talk to PostgreSQL directly.

Rate limits (#27): login and reauthentication count attempts in
``rate_limit_counters``. A per-test database starts with an empty table; the
autouse :func:`empty_rate_limit_counters` empties it before each test in every
database shared by several tests (``module_seeded_url`` and any fixture that
calls ``tests.integration.shared_databases.share_database``), and
:func:`reset_rate_limits` empties it inside a test.

Async tests run on the anyio pytest plugin (``pytest.mark.anyio``, asyncio
backend). Setup and checks that run outside a test's event loop use
``run_sql`` / ``asyncio.run`` with a short-lived ``NullPool`` engine.
"""

import asyncio
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import pytest
from docker.errors import NotFound
from pydantic import SecretStr
from sqlalchemy import make_url, pool, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.image import DockerImage
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from app.core.config import Settings
from app.core.db import async_database_url
from tests.authz.csrf import TEST_APP_ORIGIN
from tests.integration.models import TestBase
from tests.integration.shared_databases import (
    SHARED_DATABASES,
    share_database,
    unshare_database,
)

# Socket the Ryuk reaper container mounts, as seen by the Docker daemon (not the
# host). Testcontainers otherwise mounts the host-side client socket, which on
# Docker Desktop for macOS (~/.docker/run/docker.sock) is not mountable and makes
# Ryuk exit at startup. setdefault keeps any value the developer already exports
# (for example rootless Docker).
os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")

# Must match the db image in compose.yaml.
# Pin set by _docs/adr/0001-runtime-version-pins.md (accepted).
POSTGRES_IMAGE = "postgres:18"

REPO_ROOT = Path(__file__).resolve().parents[4]
PGBOUNCER_BUILD_CONTEXT = REPO_ROOT / "infrastructure" / "docker" / "pgbouncer"
PGBOUNCER_TEST_REPOSITORY = "classora-pgbouncer-test"
# `make test` in Docker (#672 QA D3) sets CLASSORA_TEST_RUN per run: every
# container and image the tests start carries it as a label, so Ctrl-C removes
# this run's databases even when Ryuk does not. Unset natively: no label.
TEST_RUN_LABELS = (
    {"classora.test-run": os.environ["CLASSORA_TEST_RUN"]}
    if os.environ.get("CLASSORA_TEST_RUN")
    else {}
)
# Server connections per database/user pair in the test PgBouncer. Smaller than
# the concurrency the tests use, so server connections must be shared.
PGBOUNCER_POOL_SIZE = 5


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def reset_rate_limits(url: str) -> None:
    """Empty ``rate_limit_counters`` in ``url`` (every policy, key and window)."""
    run_sql(url, "DELETE FROM rate_limit_counters")


@pytest.fixture(autouse=True)
def empty_rate_limit_counters() -> None:
    """Every test starts with no rate-limit counts (#27 G5)."""
    for url in sorted(SHARED_DATABASES):
        reset_rate_limits(url)


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer(
        POSTGRES_IMAGE,
        username="classora_test",
        password="classora_test",
        dbname="classora_test",
        driver=None,
        labels=TEST_RUN_LABELS,
    ) as container:
        yield container


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    """Plain ``postgresql://`` DSN of the container's default database."""
    return postgres_container.get_connection_url(driver=None)


@pytest.fixture(scope="session")
def pgbouncer_image() -> Iterator[str]:
    """The PgBouncer image built once per session from the committed context.

    Every PgBouncer container (the owner's ``pgbouncer_url``, the runtime
    role's in ``test_runtime_role.py``) runs it, and it is removed only after
    the last of them has stopped: two builds of the same context share one
    image ID, so a module-scoped removal failed with 409 while the session
    container still ran it (#165 QA round 1). Removing the session's tag
    deletes the image once no other tag (another session's) refers to it.
    """
    # A tag of its own per session (#672 D3): concurrent sessions on one Docker
    # daemon (`make test` runs SHARDS of them) build the same image ID, so
    # each removes only its own tag, never the image by ID while another
    # session's container still runs it.
    tag = f"{PGBOUNCER_TEST_REPOSITORY}:{uuid.uuid4().hex[:12]}"
    image = DockerImage(
        path=PGBOUNCER_BUILD_CONTEXT, tag=tag, clean_up=False, labels=TEST_RUN_LABELS
    )
    with image:
        try:
            yield tag
        finally:
            with suppress(NotFound):
                image.get_docker_client().client.images.remove(tag)


@contextmanager
def started_pgbouncer(
    postgres_container: PostgresContainer, image: str, user: str, password: str
) -> Iterator[str]:
    """A PgBouncer container of ``image`` (:func:`pgbouncer_image`) with the
    committed config, whose auth file holds only ``user``; yields the DSN of
    the container's default database through it, as ``user``."""
    dbname = postgres_container.dbname
    # The committed config, with only the server pool size lowered.
    command = (
        f"sed 's/^default_pool_size = .*/default_pool_size = {PGBOUNCER_POOL_SIZE}/'"
        " /etc/pgbouncer/pgbouncer.ini > /tmp/pgbouncer.ini"
        f" && grep -q '^default_pool_size = {PGBOUNCER_POOL_SIZE}$' /tmp/pgbouncer.ini"
        " && exec pgbouncer /tmp/pgbouncer.ini"
    )
    # PgBouncer reaches the session PostgreSQL container by its address on the
    # default bridge network, under the name ``db`` its config uses. (Attaching
    # the running PostgreSQL container to a second network broke connections
    # through its published port on Docker Desktop.)
    postgres_ip = postgres_container.get_docker_client().bridge_ip(
        postgres_container.get_wrapped_container().id
    )
    container = (
        DockerContainer(image)
        .with_kwargs(extra_hosts={"db": postgres_ip}, labels=TEST_RUN_LABELS)
        .with_env("PGBOUNCER_AUTH_USER", user)
        .with_env("PGBOUNCER_AUTH_PASSWORD", password)
        .with_env("POSTGRES_DB", dbname)
        .with_command(["sh", "-c", command])
        .with_exposed_ports(6432)
        .waiting_for(LogMessageWaitStrategy("process up"))
    )
    with container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6432)
        yield f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


@pytest.fixture(scope="session")
def pgbouncer_url(
    postgres_container: PostgresContainer, pgbouncer_image: str
) -> Iterator[str]:
    """Plain ``postgresql://`` DSN of the container's default database, through
    a Testcontainers PgBouncer that authenticates the container's superuser
    (the owner). The runtime-role PgBouncer is ``test_runtime_role.py``'s."""
    with started_pgbouncer(
        postgres_container,
        pgbouncer_image,
        postgres_container.username,
        postgres_container.password,
    ) as url:
        yield url


def settings_for(
    dsn: str, *, direct_dsn: str | None = None, session_cookie_secure: bool = True
) -> Settings:
    """Settings for ``dsn``; the direct URL is ``dsn`` too unless given.

    Most integration tests talk to the Testcontainers PostgreSQL directly, so
    both URLs point at it. The PgBouncer tests pass the pooler as ``dsn``.
    ``session_cookie_secure=False`` selects the local session cookie (#23).
    """
    return Settings(
        app_env="local",
        app_origin=TEST_APP_ORIGIN,
        database_url=SecretStr(dsn),
        database_direct_url=SecretStr(direct_dsn or dsn),
        session_cookie_secure=session_cookie_secure,
    )


def short_lived_engine(dsn: str, **kwargs: Any) -> AsyncEngine:
    return create_async_engine(
        async_database_url(settings_for(dsn)), poolclass=pool.NullPool, **kwargs
    )


async def run_sql_async(
    dsn: str,
    sql: str,
    params: dict[str, Any] | None = None,
    *,
    autocommit: bool = False,
) -> list[Any]:
    """:func:`run_sql` for async tests (inside their event loop).

    ``params`` are bound to ``:name`` placeholders in ``sql``.
    """
    options = {"isolation_level": "AUTOCOMMIT"} if autocommit else {}
    engine = short_lived_engine(dsn, **options)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params or {})
            return list(result.all()) if result.returns_rows else []
    finally:
        await engine.dispose()


def run_sql(
    dsn: str,
    sql: str,
    params: dict[str, Any] | None = None,
    *,
    autocommit: bool = False,
) -> list[Any]:
    """Run one statement in its own connection and return all rows (if any)."""
    return asyncio.run(run_sql_async(dsn, sql, params, autocommit=autocommit))


@pytest.fixture
def fresh_database_url(database_url: str) -> Iterator[str]:
    """An empty database created for this test only (dropped afterwards)."""
    name = f"test_{uuid.uuid4().hex}"
    run_sql(database_url, f'CREATE DATABASE "{name}"', autocommit=True)
    try:
        yield (
            make_url(database_url)
            .set(database=name)
            .render_as_string(hide_password=False)
        )
    finally:
        run_sql(database_url, f'DROP DATABASE "{name}" WITH (FORCE)', autocommit=True)


@pytest.fixture
def tables_url(fresh_database_url: str) -> str:
    """A fresh database with the test-only tables created."""

    async def _create() -> None:
        engine = short_lived_engine(fresh_database_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(TestBase.metadata.create_all)
        finally:
            await engine.dispose()

    asyncio.run(_create())
    return fresh_database_url


@pytest.fixture
def head_url(fresh_database_url: str) -> str:
    """A fresh database migrated to the Alembic head (no reference data)."""
    from alembic import command

    from tests.integration.test_migrations import alembic_config

    command.upgrade(alembic_config(fresh_database_url), "head")
    return fresh_database_url


@pytest.fixture
def seeded_url(head_url: str) -> str:
    """A fresh database at head with every reference seed applied."""
    from app.seeds import runner

    async def _seed() -> None:
        engine = runner.create_seed_engine(settings_for(head_url))
        try:
            await runner.run_reference_seeds(engine)
        finally:
            await engine.dispose()

    asyncio.run(_seed())
    return head_url


@pytest.fixture(scope="module")
def module_seeded_url(database_url: str) -> Iterator[str]:
    """A database at head with every reference seed applied, shared by the
    tests of one module (they must not depend on each other's rows)."""
    from alembic import command

    from app.seeds import runner
    from tests.integration.test_migrations import alembic_config

    name = f"test_{uuid.uuid4().hex}"
    run_sql(database_url, f'CREATE DATABASE "{name}"', autocommit=True)
    url = make_url(database_url).set(database=name).render_as_string(False)
    try:
        command.upgrade(alembic_config(url), "head")

        async def _seed() -> None:
            engine = runner.create_seed_engine(settings_for(url))
            try:
                await runner.run_reference_seeds(engine)
            finally:
                await engine.dispose()

        asyncio.run(_seed())
        share_database(url)
        yield url
    finally:
        unshare_database(url)
        run_sql(database_url, f'DROP DATABASE "{name}" WITH (FORCE)', autocommit=True)
