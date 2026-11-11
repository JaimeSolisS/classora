"""The least-privilege runtime role ``classora_app`` (M10-10, #165).

Migration 0096 grants ``classora_app`` DML only; the API (through PgBouncer
and its LISTEN connection) and the worker log in as it, while Alembic, the
seeds and the CLI stay the owner (ADR-0003 amendment). The rest of the
integration suite runs as the Testcontainers superuser (#165 E3 (a)); this
module proves the runtime role itself:

- the privilege matrix at head, pinned over every table and sequence (AC 2);
- no DDL, no bypass of the append-only rules, directly and through a
  PgBouncer whose auth file holds only ``classora_app`` (AC 3);
- no trigger in ``public`` can be disabled or dropped (AC 4);
- real work as the runtime role: an audited sign-in through PgBouncer, a job
  deferred and run by a worker, a retention run, LISTEN (AC 5);
- default privileges for tables a later migration adds (AC 6);
- ``sync-runtime-role`` against PostgreSQL (AC 8) and the fail-closed
  migration as ``classora_app`` (AC 11).

The role is cluster-wide: the module's fixture gives it ``LOGIN`` and a test
password through ``sync_runtime_role`` as the superuser.
"""

import asyncio
import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import make_url
from sqlalchemy.exc import DBAPIError
from testcontainers.community.postgres import PostgresContainer

from app.core import jobs
from app.core.db import RUNTIME_ROLE, create_sessionmaker
from app.core.runtime_role import sync_runtime_role
from app.core.uow import UnitOfWork
from app.main import create_app
from app.modules.administration import retention
from app.modules.administration.live import NotificationListener
from app.modules.administration.notifications import NOTIFICATION_CHANNEL
from app.modules.identity.permissions import OWNER
from tests.authz.csrf import ORIGIN_HEADER, origin_for
from tests.integration.auth_support import create_user, login
from tests.integration.conftest import (
    run_sql,
    run_sql_async,
    settings_for,
    short_lived_engine,
    started_pgbouncer,
)
from tests.integration.test_migrations import alembic_config, version_nums

RUNTIME_PASSWORD = "classora_app_test_only"
INSUFFICIENT_PRIVILEGE = "42501"


def as_runtime(url: str) -> str:
    """``url`` (same host and database) as ``classora_app``."""
    return (
        make_url(url)
        .set(username=RUNTIME_ROLE, password=RUNTIME_PASSWORD)
        .render_as_string(hide_password=False)
    )


def with_database(url: str, database_url: str) -> str:
    """``url`` (a host, a port and a user) pointing at ``database_url``'s database."""
    return (
        make_url(url)
        .set(database=make_url(database_url).database)
        .render_as_string(hide_password=False)
    )


async def _set_login(owner_url: str) -> None:
    engine = short_lived_engine(owner_url)
    try:
        async with engine.begin() as connection:
            await sync_runtime_role(connection, password=RUNTIME_PASSWORD)
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def runtime_url(module_seeded_url: str) -> str:
    """The module's seeded database (0096 created the role) as
    ``classora_app``, which now has ``LOGIN`` and the test password."""
    asyncio.run(_set_login(module_seeded_url))
    return as_runtime(module_seeded_url)


@pytest.fixture(scope="module")
def runtime_pgbouncer_url(
    postgres_container: PostgresContainer, runtime_url: str
) -> Iterator[str]:
    """The module's database through a PgBouncer that knows only
    ``classora_app`` (the committed image, config and entrypoint)."""
    with started_pgbouncer(
        postgres_container,
        RUNTIME_ROLE,
        RUNTIME_PASSWORD,
        tag="classora-pgbouncer-runtime-test:latest",
    ) as url:
        yield with_database(url, runtime_url)


def sqlstate_and_message(dsn: str, sql: str) -> tuple[str | None, str]:
    """Run ``sql``, which must fail; its SQLSTATE and message."""
    with pytest.raises(DBAPIError) as raised:
        run_sql(dsn, sql)
    orig = raised.value.orig
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return sqlstate, str(orig)


# --- AC 2: the privilege matrix at head -------------------------------------------

TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
DML = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})
# The only exceptions to "DML on every table" (#165 E2 (a)). A later
# append-only table adds its own REVOKE and its line here (data-model §5).
EXPECTED_EXCEPTIONS = {
    "alembic_version": frozenset({"SELECT"}),
    "audit_events": frozenset({"SELECT", "INSERT"}),
}


def table_privileges(owner_url: str) -> dict[str, frozenset[str]]:
    """Every table and view in ``public`` -> the privileges ``classora_app`` has."""
    columns = ", ".join(
        f"has_table_privilege('classora_app', c.oid, '{p}') AS p_{p.lower()}"
        for p in TABLE_PRIVILEGES
    )
    rows = run_sql(
        owner_url,
        f"SELECT c.relname, {columns} FROM pg_class c"  # noqa: S608 - constants
        " WHERE c.relnamespace = 'public'::regnamespace"
        " AND c.relkind IN ('r', 'p', 'v', 'm', 'f') ORDER BY c.relname",
    )
    return {
        row.relname: frozenset(p for p in TABLE_PRIVILEGES if getattr(row, f"p_{p.lower()}"))
        for row in rows
    }


def test_every_table_has_dml_except_the_pinned_exceptions(runtime_url, module_seeded_url):
    privileges = table_privileges(module_seeded_url)

    assert len(privileges) > 100
    assert "procrastinate_jobs" in privileges
    expected = {
        table: EXPECTED_EXCEPTIONS.get(table, DML) for table in privileges
    }
    assert privileges == expected


def test_every_sequence_has_usage_and_select_only(runtime_url, module_seeded_url):
    rows = run_sql(
        module_seeded_url,
        "SELECT c.relname,"
        " has_sequence_privilege('classora_app', c.oid, 'USAGE') AS p_usage,"
        " has_sequence_privilege('classora_app', c.oid, 'SELECT') AS p_select,"
        " has_sequence_privilege('classora_app', c.oid, 'UPDATE') AS p_update"
        " FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace"
        " AND c.relkind = 'S'",
    )

    assert rows
    assert [r.relname for r in rows if not (r.p_usage and r.p_select) or r.p_update] == []


def test_schema_and_database_privileges(runtime_url, module_seeded_url):
    [row] = run_sql(
        module_seeded_url,
        "SELECT has_schema_privilege('classora_app', 'public', 'CREATE') AS p_create,"
        " has_schema_privilege('classora_app', 'public', 'USAGE') AS p_usage,"
        " has_database_privilege('classora_app', current_database(), 'TEMPORARY')"
        " AS p_temporary,"
        " has_database_privilege('classora_app', current_database(), 'CONNECT')"
        " AS p_connect,"
        " has_database_privilege('classora_app', current_database(), 'CREATE')"
        " AS p_create_schema",
    )

    assert (row.p_create, row.p_usage, row.p_temporary, row.p_connect, row.p_create_schema) == (
        False,
        True,
        False,
        True,
        False,
    )


def test_the_role_has_no_special_attribute_no_membership_and_owns_nothing(
    runtime_url, module_seeded_url
):
    [role] = run_sql(
        module_seeded_url,
        "SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication,"
        " rolbypassrls, rolinherit, rolcanlogin FROM pg_roles"
        " WHERE rolname = 'classora_app'",
    )
    [counts] = run_sql(
        module_seeded_url,
        "SELECT"
        " (SELECT count(*) FROM pg_auth_members m"
        "   WHERE m.member = 'classora_app'::regrole) AS memberships,"
        " (SELECT count(*) FROM pg_class"
        "   WHERE relowner = 'classora_app'::regrole) AS relations,"
        " (SELECT count(*) FROM pg_proc"
        "   WHERE proowner = 'classora_app'::regrole) AS functions,"
        " (SELECT count(*) FROM pg_type"
        "   WHERE typowner = 'classora_app'::regrole) AS types,"
        " (SELECT count(*) FROM pg_namespace"
        "   WHERE nspowner = 'classora_app'::regrole) AS schemas",
    )

    assert tuple(role) == (False, False, False, False, False, False, True)
    assert tuple(counts) == (0, 0, 0, 0, 0)
