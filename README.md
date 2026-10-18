# Classora

Classora is a management platform for a single tutoring center. It covers class
scheduling and enrollment, attendance, academic records, documents, billing and
payments, tutor payroll and operational reporting for owners, administrators,
coordinators, finance staff, tutors, students and guardians. It is built as a
FastAPI modular monolith with a React single-page client, backed by PostgreSQL.

## Status

**Pre-alpha.** Only the local development foundation exists so far: a FastAPI
API with liveness and readiness health checks, backed by a local PostgreSQL
container (reached through PgBouncer) whose schema is managed with Alembic
migrations, a background worker for queued and periodic jobs (Procrastinate),
and a React +
TypeScript + Vite placeholder page that shows whether the API is live, calling
it through a typed client generated from the API's OpenAPI document. The web
app has its design system foundation (central design tokens, Tailwind CSS,
shadcn/ui components catalogued in Storybook) and a light/dark/system theme
switch. No product features are implemented yet.

## Prerequisites

- [Docker Engine](https://docs.docker.com/engine/) with Docker Compose v2
- GNU Make
- [uv](https://docs.astral.sh/uv/) (installs the pinned Python version; no other Python install is needed)
- [Node.js](https://nodejs.org/) 24 (see `.nvmrc`; with nvm, run `nvm use`). pnpm
  runs through Corepack, which ships with Node 24, so no global pnpm install is
  needed. Other Node majors are rejected with an engine error.

## Getting started

```bash
cp .env.example .env
make dev
```

`make dev` builds the images and starts PostgreSQL, PgBouncer, the API, the
background worker and the web app in the background, and returns once all of
them are healthy. The API reaches PostgreSQL through PgBouncer (connection
pooling). Then open:

- Web app: <http://127.0.0.1:5173> (shows `API: live` when the API is up)
- Liveness: <http://127.0.0.1:8000/health/live>
- Readiness (includes PgBouncer and PostgreSQL): <http://127.0.0.1:8000/health/ready>

The web app calls the API on its own origin: the Vite dev server proxies
`/health` and `/api` to the API. The Compose web service has no hot reload; for
that, run `corepack pnpm --filter @classora/web dev` on the host, which proxies
to `http://127.0.0.1:8000`.

The background worker (`worker`, built from the same image as the API) runs
queued and periodic jobs. It processes jobs only after `make migrate` (below)
has created its tables; until then it waits and logs that it needs
`make migrate`. Follow its logs with `docker compose logs -f worker`. In local
and dev, `curl -X POST http://127.0.0.1:8000/api/v1/dev/demo-jobs` queues a
demo job to check that the worker runs it.

Stop the stack with `docker compose down` (data is kept in a named volume;
`docker compose down -v` deletes it).

### Compose layout

The services are split into two stack files, the way the servers run them:

- Data stack, `infrastructure/docker/compose.data.yaml`: PostgreSQL (`db`) and
  PgBouncer (`pgbouncer`).
- App stack, `infrastructure/docker/compose.app.yaml`: the API (`api`), the
  background worker (`worker`) and the web app (`web`).

Locally, the root `compose.yaml` combines both into one Compose project, so
plain `docker compose` commands (`ps`, `logs api`, `logs worker`, `exec db psql ...`,
`stop`/`start`, `down`) work from the repository root. The stack files publish
no host ports for PostgreSQL or PgBouncer; only the local developer override,
`compose.override.yaml` (loaded automatically), publishes them on `127.0.0.1`:
PostgreSQL on `POSTGRES_PORT` (default 5432) and PgBouncer on `PGBOUNCER_PORT`
(default 6432).

### Database migrations

```bash
make migrate
```

`make migrate` applies the Alembic migrations to the local Compose database
(it starts the database if it is not running). Migrations connect to PostgreSQL
directly, not through PgBouncer. Neither `make dev` nor the API
runs migrations, so run `make migrate` after `make dev` on a new checkout and
again whenever you pull new migrations. Running it when the database is already
up to date changes nothing.

### Seed data

```bash
make seed       # reference data
make seed-demo  # synthetic demo data (local and dev only)
```

`make seed` applies the reference data every environment needs. Run
`make migrate` first: it refuses to run on a database with pending migrations.
It is safe to repeat; a second run changes nothing.

`make seed-demo` runs on the host (with the API's development dependencies, via
`uv`) and needs `.env`. It applies the reference data and then generates
synthetic Spanish (Mexico) demo data. It never uses real personal data, and it
refuses to run unless `APP_ENV` is `local` or `dev`. Running it again adds more
demo data; to start over, run `docker compose down -v`, then `make dev`,
`make migrate` and `make seed-demo`.

No reference seeds or demo generators exist yet, so both commands currently
write nothing; they fill in as features are added.

## Configuration

`.env.example` documents every variable with safe local placeholders; copy it
to `.env` as shown above. `.env` is git-ignored and must never be committed.
The API and the worker validate their configuration when they start and refuse
to start on a missing or invalid value; `docker compose logs api` (or
`logs worker`) shows a short `Configuration error` message naming each bad
variable. `WORKER_SHUTDOWN_TIMEOUT_SECONDS` (default 20, 1 to 30) sets how long
a stopping worker lets running jobs finish before aborting them.

The API uses two database URLs: `DATABASE_URL` is the pooled one (through
PgBouncer) and `DATABASE_DIRECT_URL` the direct one to PostgreSQL (used by
migrations, seeds and the worker). Inside Compose both are built from the `POSTGRES_*`
values; the values in `.env` only matter when you run the API, Alembic or
`make seed-demo` on the host.

If your `.env` was copied before a variable was added, refresh it from
`.env.example` (for example, copy it again).

## Running tests

Docker must be running: API integration tests start a throwaway PostgreSQL
container (and a PgBouncer container built from this repository) with
[Testcontainers](https://testcontainers.com/). The Compose
stack and `.env` are not needed.

```bash
make test              # all tests: API (unit + integration) then web (Vitest)
make test-api          # all API tests with a coverage report
make test-web          # web and API client tests (Vitest, no Docker needed)
make test-unit         # API unit tests only (no Docker needed)
make test-integration  # API integration tests only
make test-migrations   # API migration checks only (upgrade, round trip, no model drift)
```

Storybook story tests run separately, on the host:

```bash
make test-storybook    # every story in headless Chromium, with axe checks in light and dark
```

The first run downloads the Playwright headless Chromium (about 100 MB); later
runs reuse it.

Each API run prints a terminal coverage report for the API (`app/`), with
branch coverage and missing lines. `make test` does not start the Compose database,
and it can run while `make dev` is up.

## Linting and type checks

```bash
make lint      # both apps
make lint-api  # Ruff (lint and format check) and mypy (strict) on the API
make lint-web  # ESLint, Prettier check and TypeScript (strict) on the web app and the API client
```

Lint targets only check and never modify files. The web targets install
dependencies from the committed lockfile first, so no manual `pnpm install` is
needed. `make lint` and `make test` fail if either app fails; use `make -k` to
run both parts regardless.

To build the web app for production: `make build-web` (output in
`apps/web/dist/`).

## API client

The web app calls the API through `@classora/api-client`
(`packages/api-client`), a typed client generated from the API's OpenAPI
document. After any API change, regenerate it and commit the result:

```bash
make api-client        # export packages/api-client/openapi.json, then regenerate the client types
make check-api-client  # regenerate and fail if packages/api-client differs from the commit (CI drift check)
```

Both need uv and Node 24, but not Docker, a database or `.env`. API conventions
(paths, Problem Details errors, pagination, versions) are in
[`_docs/api.md`](_docs/api.md).

## Storybook

```bash
make storybook
```

Opens the component catalog at <http://127.0.0.1:6006> (no browser is opened
for you). The toolbar switches stories between light and dark. It fails
instead of using another port when 6006 is taken. To build a static copy:
`corepack pnpm --filter @classora/web build-storybook` (output in
`apps/web/storybook-static/`).

## Continuous integration

GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on
every pull request to `develop` or `main` and on pushes to both. Each check is
one `make` target, so a failing check can be reproduced locally with the same
command:

| CI job | Local command |
| --- | --- |
| `api-lint` | `make lint-api` |
| `api-test` | `make test-unit`, `make test-migrations`, `make test-integration` |
| `web` | `make lint-web`, `make test-web`, `make build-web` |
| `web-storybook` | `make test-storybook` |
| `api-client` | `make check-api-client` |

Pull requests run only the jobs their changed files can affect (API jobs for
`apps/api/` and the PgBouncer image, web jobs for the web app, its packages and the Node toolchain
files; both for `Makefile` and workflow changes; neither for documentation).
The `api-client` drift check runs whenever the API or web jobs run.
`ci-required` is the one required check: it reports on every pull request and
fails if any job that ran failed or was cancelled.

## Documentation

- [Product specification](_docs/spec.md)
- [Architecture](_docs/architecture.md)
- [Design system](_docs/design-system.md) (tokens, theming, accessibility rules)
- [API conventions](_docs/api.md) (paths, errors, pagination, versions, client workflow)
- [Agent instructions](AGENTS.md)
- [Delivery process](_docs/process.md) (agent workflow and contribution guidance)
