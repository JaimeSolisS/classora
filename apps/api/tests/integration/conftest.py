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

Async tests run on the anyio pytest plugin (``pytest.mark.anyio``, asyncio
backend). Setup and checks that run outside a test's event loop use
``run_sql`` / ``asyncio.run`` with a short-lived ``NullPool`` engine.
"""

import asyncio
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import make_url, pool, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.image import DockerImage
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from app.core.config import Settings
from app.core.db import async_database_url
from tests.integration.models import TestBase

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
# Server connections per database/user pair in the test PgBouncer. Smaller than
# the concurrency the tests use, so server connections must be shared.
PGBOUNCER_POOL_SIZE = 5


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer(
        POSTGRES_IMAGE,
        username="classora_test",
        password="classora_test",
        dbname="classora_test",
        driver=None,
    ) as container:
        yield container


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    """Plain ``postgresql://`` DSN of the container's default database."""
    return postgres_container.get_connection_url(driver=None)


@pytest.fixture(scope="session")
def pgbouncer_url(postgres_container: PostgresContainer) -> Iterator[str]:
    """Plain ``postgresql://`` DSN of the container's default database, through
    a Testcontainers PgBouncer built from the committed image and config."""
    user = postgres_container.username
    password = postgres_container.password
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
    with DockerImage(
        path=PGBOUNCER_BUILD_CONTEXT, tag="classora-pgbouncer-test:latest"
    ) as image:
        container = (
            DockerContainer(str(image))
            .with_kwargs(extra_hosts={"db": postgres_ip})
            .with_env("POSTGRES_USER", user)
            .with_env("POSTGRES_PASSWORD", password)
            .with_env("POSTGRES_DB", dbname)
            .with_command(["sh", "-c", command])
            .with_exposed_ports(6432)
            .waiting_for(LogMessageWaitStrategy("process up"))
        )
        with container:
            host = container.get_container_host_ip()
            port = container.get_exposed_port(6432)
            yield f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


def settings_for(dsn: str, *, direct_dsn: str | None = None) -> Settings:
    """Settings for ``dsn``; the direct URL is ``dsn`` too unless given.

    Most integration tests talk to the Testcontainers PostgreSQL directly, so
    both URLs point at it. The PgBouncer tests pass the pooler as ``dsn``.
    """
    return Settings(
        app_env="local",
        database_url=SecretStr(dsn),
        database_direct_url=SecretStr(direct_dsn or dsn),
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
