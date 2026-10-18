# ADR-0003: Database connection topology and background jobs

## Status

Accepted

## Date

2026-10-17

## Sign-off

Owner: JaimeSolisS. Approved as proposed on 2026-10-17 (GitHub issue #6, orchestrator decision record).

## Context

Resolves architecture §10 **ISSUE-D1** (PgBouncer vs asyncpg, LISTEN/NOTIFY and Procrastinate), **ISSUE-J1** (Procrastinate driver and transactional enqueue) and **ISSUE-J2** (where the periodic scheduler runs) in [`../architecture.md`](../architecture.md) §10.3 and §10.4. It also records the three details approved on #6 as escalation E1, option (a).

- Spec [`../spec.md`](../spec.md) §10 names "SQLAlchemy + PgBouncer" for the connection pool, async SQLAlchemy with asyncpg for the ORM, "SSE and LISTEN/NOTIFY", and "Procrastinate on PostgreSQL; dedicated worker container; periodic scheduler" for background work.
- PgBouncer's usual **transaction pooling** mode breaks `LISTEN`, which is session-scoped, and conflicts with asyncpg's prepared-statement cache.
- Procrastinate's worker relies on `LISTEN/NOTIFY` and advisory locks. Alembic migrations may need session-level features.
- Procrastinate's connectors are psycopg-based and it has no asyncpg connector (verified for the pinned version below). So enqueueing a job is not atomic with the business transaction that triggered it.
- In Procrastinate, periodic tasks are deferred by the worker process itself, with database-level deduplication.
- Architecture §5 (data), §6 (background processing) and §9.1 (data stack and app stack per host).

## Decision

### Connection paths (ISSUE-D1)

- API request traffic goes through **PgBouncer in transaction mode**.
  - asyncpg statement caching is disabled: `prepared_statement_cache_size=0` in SQLAlchemy and `statement_cache_size=0` in asyncpg.
  - Prepared statements get unique names.
- These connect **directly** to PostgreSQL on the internal Docker network:
  1. the one SSE `LISTEN` connection per API worker process;
  2. the Procrastinate worker;
  3. Alembic.
- PgBouncer runs in the **data stack**, next to PostgreSQL.

### Details approved as E1 option (a) on #6

- **(a) API-side job deferral.** The API process uses Procrastinate's async psycopg connector (`PsycopgConnector`) through PgBouncer, with psycopg server-side prepares disabled (`prepare_threshold=None`). The only API connection that bypasses PgBouncer is the SSE `LISTEN` connection.
- **(b) Worker business sessions.** Job code in the `worker` container that uses SQLAlchemy/asyncpg connects directly to PostgreSQL, like the worker's Procrastinate connector. The whole `worker` container counts as "the worker".
- **(c) Procrastinate schema.** Alembic owns it. One Alembic revision applies the Procrastinate schema for the pinned version. Each later Procrastinate upgrade adds an Alembic revision that applies that version's shipped migration SQL. The worker never applies or changes the schema at start-up. M1-16 implements this.

### Driver and enqueue (ISSUE-J1)

- psycopg 3 is accepted as a dependency used **only** by Procrastinate.
- Application and ORM code import only asyncpg (through SQLAlchemy) and never import `psycopg` directly.
- Procrastinate extras that pull in psycopg2 or aiopg (`aiopg`, `psycopg2`, `sqlalchemy`) are not installed.
- Jobs are enqueued **after commit**, so enqueue is not atomic with the business transaction.
- Every job must be idempotent. The mechanics are left to ISSUE-J4 / ADR-0006.
- Periodic **sweeper** tasks find business records that need work but have no job (for example, enrollments without charges) and enqueue them. This covers a crash between commit and enqueue. There is no outbox table.

### Periodic scheduling (ISSUE-J2)

- Periodic deferral runs inside the single `worker` container. There is no separate scheduler container.
- Every periodic task uses a Procrastinate `queueing_lock` and an idempotent business key.

### Pinned versions (for M1-16)

- Procrastinate **3.10.0**, constraint `procrastinate>=3.10.0,<4`.
- psycopg **3.3.6** (with `psycopg-pool` 3.3.3), resolved through Procrastinate's `psycopg[pool]` requirement.

## Verification

Check date: **2026-10-17**. All commands ran outside the repository, in a scratch directory. No repository file was touched, and Procrastinate and psycopg were not added to `apps/api/pyproject.toml` or `apps/api/uv.lock`.

**1. Newest release on PyPI** (<https://pypi.org/pypi/procrastinate/json>, <https://pypi.org/project/procrastinate/3.10.0/>):

```bash
curl -s https://pypi.org/pypi/procrastinate/json | python3 -c 'import json,sys;i=json.load(sys.stdin)["info"];print(i["version"],i["requires_python"]);print(i["requires_dist"])'
```

```text
3.10.0 >=3.10
['asgiref', 'attrs', 'croniter', 'packaging', 'psycopg[pool]', 'python-dateutil', 'typing-extensions', 'aiopg; extra == "aiopg"', 'psycopg2-binary; extra == "aiopg"', 'django>=2.2; extra == "django"', 'psycopg2-binary; extra == "psycopg2"', 'sphinx; extra == "sphinx"', 'sqlalchemy~=2.0; extra == "sqlalchemy"']
```

3.10.0 (uploaded 2026-09-23) is the newest release; no later or pre-release version exists. psycopg on PyPI: `3.3.6`, `Requires-Python >=3.10`. Procrastinate's classifiers list no Python versions (`Development Status :: 5 - Production/Stable`, `Intended Audience :: Developers`, `License :: OSI Approved :: MIT License`).

**2. Installed package on Python 3.14** (throwaway environment):

```bash
uv run --isolated --no-project --python 3.14 --with "procrastinate==3.10.0" --with "psycopg[binary]" python -I check.py
```

`check.py` prints `sys.version`, the installed versions, the names in `dir(procrastinate)` containing `Connector`, every submodule (`pkgutil.walk_packages`), the package path, whether psycopg2/aiopg/asyncpg/sqlalchemy are installed, and the package metadata. Key output:

```text
3.14.0 (main, Nov 19 2025, 23:40:18) [Clang 21.1.4 ]
procrastinate 3.10.0 psycopg 3.3.6 psycopg-pool 3.3.3 pq impl binary libpq 180006
['AiopgConnector', 'BaseConnector', 'Psycopg2Connector', 'PsycopgConnector', 'SyncPsycopgConnector']
psycopg2 NOT INSTALLED
psycopg2-binary NOT INSTALLED
aiopg NOT INSTALLED
asyncpg NOT INSTALLED
sqlalchemy NOT INSTALLED
Requires-Python: >=3.10
dists: ['asgiref', 'attrs', 'croniter', 'packaging', 'procrastinate', 'psycopg', 'psycopg-binary', 'psycopg-pool', 'python-dateutil', 'six', 'typing_extensions']
```

- `PsycopgConnector` (async, psycopg 3) and `SyncPsycopgConnector` (sync, psycopg 3) are public.
- `AiopgConnector` and `Psycopg2Connector` in the top-level namespace are `_MovedElsewhere` placeholders pointing at `procrastinate.contrib.aiopg` and `procrastinate.contrib.psycopg2`. Those need the `aiopg` / `psycopg2` extras, which are not part of the default install (psycopg2, aiopg and SQLAlchemy are absent from the environment above).
- Submodules include `procrastinate.psycopg_connector`, `procrastinate.sync_psycopg_connector`, `procrastinate.contrib.{aiopg,django,psycopg2,sphinx,sqlalchemy}`, `procrastinate.schema` and `procrastinate.sql.migrations`. There is no asyncpg module.

**3. No asyncpg anywhere in the package:**

```bash
grep -ril asyncpg <site-packages>/procrastinate    # no output, exit 1
```

**4. Schema mechanics for decision (c):**

```bash
ls <site-packages>/procrastinate/sql <site-packages>/procrastinate/sql/migrations
uv run --isolated --no-project --python 3.14 --with "procrastinate==3.10.0" --with "psycopg[binary]" procrastinate schema --help
```

- Full schema: `procrastinate/sql/schema.sql`. Queries: `procrastinate/sql/queries.sql`.
- Versioned migration SQL: `procrastinate/sql/migrations/` (38 `.sql` files; 40 entries counting `__init__.py` and `__pycache__`; from `00.00.00_01_initial.sql` to `03.04.00_50_post_add_retry_failed_job_procedure.sql`), plus `procrastinate/sql/future_migrations/`.
- `procrastinate.schema.SchemaManager` exposes `get_schema()` and `get_migrations_path()`. The CLI offers `procrastinate schema [--apply] [--read] [--migrations-path]`, so an Alembic revision can read the shipped SQL without the worker applying it.

**5. Default install and libpq (note for M1-16).** The default install (`procrastinate==3.10.0` only) imports psycopg's pure-Python implementation, which needs the system libpq. On the macOS host and in `python:3.14-slim` without libpq, `import procrastinate` fails with `ImportError: no pq wrapper available … libpq library not found`. In `python:3.14-slim` with `libpq5` installed it imports and works:

```bash
docker run --rm -v "$PWD/check.py:/check.py:ro" python:3.14-slim sh -c 'pip install -q "procrastinate==3.10.0"; python -c "import procrastinate"; apt-get update -qq && apt-get install -y -qq libpq5; python -I /check.py'
```

```text
- couldn't import psycopg 'python' implementation: libpq library not found
3.14.7 (main, Sep  1 2026, 00:05:20) [GCC 14.2.0]
procrastinate 3.10.0 psycopg 3.3.6 psycopg-pool 3.3.3 pq impl python libpq 170011
['AiopgConnector', 'BaseConnector', 'Psycopg2Connector', 'PsycopgConnector', 'SyncPsycopgConnector']
```

This is a psycopg runtime requirement, not a Procrastinate limitation. How the API image provides libpq (a system package or a psycopg wheel extra) is left to M1-16.

**6. Upstream source and docs.**

- Release tag: <https://github.com/procrastinate-org/procrastinate/releases/tag/3.10.0> (tag `3.10.0`, commit `8b6398329d915b7299c0d936e567facaebea8903`).
- Connector docs at the tag: <https://github.com/procrastinate-org/procrastinate/blob/3.10.0/docs/howto/basics/connector.md>. It lists five connectors: `PsycopgConnector` and `AiopgConnector` (async), and `SyncPsycopgConnector`, `Psycopg2Connector` and `SQLAlchemyPsycopg2Connector` (sync, defer only). None is asyncpg-based. Read the Docs publishes only `stable` and `main` (no per-version page); the rendered page is <https://procrastinate.readthedocs.io/en/stable/howto/basics/connector.html>.
- **PostgreSQL 18 is in the upstream CI matrix** at the tag: <https://github.com/procrastinate-org/procrastinate/blob/3.10.0/.github/workflows/ci.yml> runs `python-version: "3.14"` with `postgres-version: "18"`, and the acceptance tests use `image: postgres:18`.

**Result:** all checks pass. Procrastinate 3.10.0 installs and imports on Python 3.14, has psycopg 3 async and sync connectors, has no asyncpg connector, keeps psycopg2/aiopg behind optional extras, and ships the schema and versioned migration SQL needed for decision (c).

## Consequences

- The API holds two kinds of database connection: pooled request traffic and Procrastinate deferral through PgBouncer, and one direct `LISTEN` connection per worker process. The `worker` container connects directly for everything.
- psycopg 3 joins asyncpg as a second PostgreSQL driver, limited to Procrastinate. Code review (and any later import check) must keep `psycopg` out of application and ORM code.
- A crash between commit and enqueue leaves work without a job until a sweeper finds it, so sweepers and idempotent jobs are required, not optional.
- Procrastinate upgrades are Alembic changes: each upgrade must add a revision with that version's migration SQL.
- M1-16 adds Procrastinate and psycopg to `apps/api/pyproject.toml` and `apps/api/uv.lock` with the versions above, and decides how libpq is provided (Verification step 5). Outcome (M1-16, #16 E1): Debian's `libpq5` in the API/worker image; `psycopg[binary]` only as a dev dependency for host tests and CI.
- Still open:
  - `max_connections` and pool sizing for the micro instance (ISSUE-I8, M10-04);
  - SSE fan-out details beyond the direct `LISTEN` connection (ISSUE-N1, still PROPOSED);
  - the failed-job visibility and retry UI (ISSUE-J3);
  - idempotency mechanics for generated records (ISSUE-J4).

## Alternatives considered

- **A separate PgBouncer pool in session mode** for `LISTEN`, the worker and Alembic. Considered and rejected in favor of direct connections on the internal Docker network.
- **An asyncpg Procrastinate connector**, so the ORM and jobs share one driver and enqueue can join the business transaction. Not available: Procrastinate 3.10.0 has no asyncpg connector (see Verification).
- **A transactional outbox table** to make enqueue atomic with the business transaction. Rejected in favor of after-commit enqueue, idempotent jobs and sweepers.
- **A separate scheduler container** for periodic tasks. Rejected: periodic deferral runs inside the single `worker` container with `queueing_lock`.

## References

- [`../architecture.md`](../architecture.md) §5, §6, §9.1, §10.3 ISSUE-D1, §10.4 ISSUE-J1 and ISSUE-J2, §10.8 ISSUE-N1, §11
- [`../spec.md`](../spec.md) §10 (stack table: ORM, connection pool, background work), §15 (API and worker as separate containers)
- [ADR-0001](0001-runtime-version-pins.md) (runtime pins)
- GitHub issue #6 (this decision gate, escalation E1 option (a))
- <https://pypi.org/project/procrastinate/3.10.0/>, <https://github.com/procrastinate-org/procrastinate/releases/tag/3.10.0>
