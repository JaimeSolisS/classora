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
TypeScript + Vite web app. The web app has a routed shell (a layout with
navigation, a 404 page and central error handling) and a TanStack Query data
layer; its home page shows whether the API is live,
calling it through a typed client generated from the API's OpenAPI document.
It also has its design system foundation (central design tokens, Tailwind CSS,
shadcn/ui components catalogued in Storybook), a light/dark/system theme
switch, and Spanish and English UI text with a language switch. The API has
username and password login with server-side sessions (an HTTP-only session
cookie) that expire after a period of inactivity or a maximum lifetime, with
rate limits on login, password confirmation, one-time links and account
requests. The web app
has a sign-in page and sign-out, keeps users signed in with that session, and
shows only the pages and navigation a user's role allows. Owners and
administrators can invite people from the Invitations page: the app shows a
one-time link (no email is sent) to hand over in person or by phone, and the
invited person opens it to set their own password and then signs in. In the
same way, they can create a one-time password reset link from the Password
resets page, and every signed-in user can change their own password.
Parents, guardians and students can request an account from the sign-in page;
the account works only after an Owner or administrator confirms their
identity and approves it on the Account requests page (or rejects it).
Approving a request never enrolls anyone.
On the Users page, Owners and administrators can search and filter every
account, edit a person's name and optional contact details (never used to
sign in), create a password reset link for them, and suspend, archive or
reactivate their account; suspending or archiving asks for the password again
and ends that person's sessions, and the last active Owner can never be
suspended or archived.
Requests that change data are protected by an `Origin` check and a CSRF
token, and the web app is served with a Content Security Policy and other
security headers. No other product features are implemented yet.

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

- Web app: <http://127.0.0.1:5173>. It opens the sign-in page; after signing in
  (see [Logging in locally](#logging-in-locally)) the home page shows
  `API: live` when the API is up. Use exactly this address (`127.0.0.1`, not
  `localhost`): it is `APP_ORIGIN`, and the API refuses changes from any other
  origin.
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
and dev,
`curl -X POST -H 'Origin: http://127.0.0.1:5173' http://127.0.0.1:8000/api/v1/dev/demo-jobs`
queues a demo job to check that the worker runs it.

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
(default 6432). The API and web ports are set in the app stack file, also on
`127.0.0.1`: `API_PORT` (default 8000) and `WEB_PORT` (default 5173).

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

The reference data currently consists of the user roles and their
permissions. The demo data currently consists of one demo Owner account,
`demo.owner` with the password `classora-demo-owner`. This password is a
development placeholder: the account is created only when `APP_ENV` is `local`
(a `dev` run skips it), and a second run leaves it as it is.
`make bootstrap-owner` (below) is refused once any Owner exists, so locally use
either the demo Owner or `make bootstrap-owner`, not both.

### First Owner account

```bash
make bootstrap-owner USERNAME=<name>
```

`make bootstrap-owner` creates the first Owner account (the center's top-level
administrator). Run `make migrate` and `make seed` first. It prompts twice for
the password, which must have at least 12 characters, and works only while no
Owner account exists.

### Owner password reset

```bash
make reset-owner-password USERNAME=<name>
```

If an Owner is locked out, `make reset-owner-password` prints a one-time link
for that existing, active Owner account. Open it in a browser to set a new
password; it is shown once and expires in 24 hours, and every session of the
account ends when the new password is set.

### Logging in locally

Open the web app at <http://127.0.0.1:5173> and sign in with the demo Owner
(`demo.owner` / `classora-demo-owner`, from `make seed-demo`) or with the
account from `make bootstrap-owner`. To call the API directly, log in against
it and keep the session cookie, for example with the demo Owner:

```bash
curl -s -c cookies.txt -H 'Content-Type: application/json' \
  -H 'Origin: http://127.0.0.1:5173' \
  -d '{"username": "demo.owner", "password": "classora-demo-owner"}' \
  http://127.0.0.1:8000/api/v1/auth/login
curl -s -b cookies.txt http://127.0.0.1:8000/api/v1/auth/me
curl -s -b cookies.txt -X POST -H 'Origin: http://127.0.0.1:5173' \
  -H 'X-CSRF-Token: <csrf_token from the login response>' \
  http://127.0.0.1:8000/api/v1/auth/logout
```

Login returns the signed-in user, including a `csrf_token`, and sets the
session cookie; `GET /api/v1/auth/me` returns the same user, roles,
permissions and token, and `POST /api/v1/auth/logout` ends the session. A
failed login answers `401 invalid_credentials`. After 10 failed logins for one
username, or 50 login attempts from one address, in a 15-minute window, login
answers `429 rate_limited` until the window ends (the `Retry-After` header gives
the seconds to wait), even with the right password. Every `POST`, `PUT`,
`PATCH` or `DELETE` must send `Origin: http://127.0.0.1:5173` (the
`APP_ORIGIN`), and once signed in also the `X-CSRF-Token` header; otherwise
the API answers `403 csrf_failed`. For the same reason the Swagger UI on the
API port cannot send them. Sessions expire after 30 idle minutes (12 hours at
most) for staff accounts such as the demo Owner, and after 7 idle days (30 days
at most) for tutors, students and guardians; `/api/v1/auth/me` then answers
`401 session_expired`, so log in again. Details: [`_docs/api.md`](_docs/api.md) and
[`_docs/security-headers.md`](_docs/security-headers.md).

## Configuration

`.env.example` documents every variable with safe local placeholders; copy it
to `.env` as shown above. `.env` is git-ignored and must never be committed.
The API and the worker validate their configuration when they start and refuse
to start on a missing or invalid value; `docker compose logs api` (or
`logs worker`) shows a short `Configuration error` message naming each bad
variable. `WORKER_SHUTDOWN_TIMEOUT_SECONDS` (default 20, 1 to 30) sets how long
a stopping worker lets running jobs finish before aborting them.

`APP_ORIGIN` (required, `http://127.0.0.1:5173` in `.env.example`) is the web
app's origin. Change it together with `WEB_PORT`; `http` is accepted only with
`APP_ENV=local`. An `.env` created before it existed must get the line (or be
refreshed from `.env.example`), or the API refuses to start.

`SESSION_COOKIE_SECURE=false` (the `.env.example` value) is for local plain
HTTP only: it switches the session cookie to `classora_session` without the
`Secure` flag, and the API refuses to start with it unless `APP_ENV=local`.
Every other environment uses the default, `true` (`__Host-classora_session`,
`Secure`). An older `.env` still has `SESSION_COOKIE_SECURE=true`; refresh it
from `.env.example`.

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
make test-web          # web and API client tests (Vitest, no Docker needed; includes the es/en translation-key check)
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

End-to-end tests (Playwright) run against their own Compose stack:

```bash
make e2e               # smoke, accessibility (axe, light and dark), session-cookie and CSRF, and security-header tests in headless Chromium
```

`make e2e` builds and starts a separate Compose project, `classora-e2e`, on its
own ports (web 15173, API 18000, PostgreSQL 15432, PgBouncer 16432) with an empty
volume, applies the migrations, the reference seeds and the demo data
(`--seed 1`), runs the tests and always removes the stack again (`down -v`).
It needs Docker, uv and Node 24 but no `.env` (it uses `.env.example`), and it
can run while `make dev` is up without touching that stack or its data. A
failed run leaves Playwright traces, screenshots and the Compose logs
(`compose.log`) in `e2e/test-results/` and an HTML report in
`e2e/playwright-report/`. Open a trace from the repository root with
`corepack pnpm --filter @classora/e2e exec playwright show-trace test-results/<test>/trace.zip`
(the command runs in `e2e/`, so the path is relative to `e2e/`).
To keep the stack up for debugging, run `E2E_KEEP=1 make e2e`; the command it
prints removes it afterwards.

Each API run prints a terminal coverage report for the API (`app/`), with
branch coverage and missing lines. `make test` does not start the Compose database,
and it can run while `make dev` is up. `make test` does not run the Storybook or
E2E tests.

Rules for writing tests (layers, fixtures and test data, authorization and
financial invariant tests, E2E and accessibility) are in
[`_docs/testing-guidelines.md`](_docs/testing-guidelines.md).

## Linting and type checks

```bash
make lint      # both apps and the E2E tests
make lint-api  # Ruff (lint and format check) and mypy (strict) on the API
make lint-web  # ESLint, Prettier check and TypeScript (strict) on the web app and the API client
make lint-e2e  # TypeScript (strict) and Prettier check on the E2E tests (e2e/)
```

Lint targets only check and never modify files. The web targets install
dependencies from the committed lockfile first, so no manual `pnpm install` is
needed. `make lint` and `make test` fail if any part fails; use `make -k` to
run every part regardless.

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
| `e2e` | `make lint-e2e`, `make e2e` (on failure, uploads traces, the HTML report and the Compose logs as the `e2e-report` artifact) |
| `security-deps` | `make audit-deps` (pip-audit and `pnpm audit` over both lockfiles) |
| `security-secrets` | `make scan-secrets` (gitleaks, full history); on pull requests also `make scan-secrets-diff BASE=<sha> HEAD=<sha>` |
| `security-sast` | `make sast` (Semgrep); Ruff's security (`S`) rules run in `make lint-api` |

Pull requests run only the jobs their changed files can affect (API jobs for
`apps/api/` and the PgBouncer image, web jobs for the web app, its packages and the Node toolchain
files; the `e2e` job for changes to either app, its packages, `e2e/`, the Compose
and Docker files, the Node toolchain files or `.env.example`; all of them for
`Makefile` and workflow changes; none for documentation).
The `api-client` drift check runs whenever the API or web jobs run. The three
security jobs run on every pull request and push, documentation included.
`ci-required` is the one required check: it reports on every pull request and
fails if any job that ran failed or was cancelled, or if a security job was skipped.

### Security scans

```bash
make security                # audit-deps, scan-secrets and sast, as in CI (needs Docker, uv, Node 24)
make scan-image IMAGE=<ref>  # Trivy scan of a built image; fails on fixable CRITICAL findings
```

Scanner versions are pinned in the `Makefile`. When a scan fails, fix the
cause (upgrade the dependency, remove the secret, change the code). If that is
not possible, add an exception with a reason and a review date as described in
[ADR-0016](_docs/adr/0016-ci-security-tooling.md); it is reviewed in the pull
request.

## Documentation

- [Product specification](_docs/spec.md)
- [Architecture](_docs/architecture.md)
- [Design system](_docs/design-system.md) (tokens, theming, accessibility rules)
- [API conventions](_docs/api.md) (paths, errors, pagination, versions, client workflow)
- [Security headers](_docs/security-headers.md) (Content Security Policy, API headers, `Origin` and CSRF)
- [Testing guidelines](_docs/testing-guidelines.md) (test layers, fixtures, permission matrix, financial invariants, E2E, accessibility)
- [Agent instructions](AGENTS.md)
- [Delivery process](_docs/process.md) (agent workflow and contribution guidance)
