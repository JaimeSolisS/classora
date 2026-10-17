# Classora

Classora is a management platform for a single tutoring center. It covers class
scheduling and enrollment, attendance, academic records, documents, billing and
payments, tutor payroll and operational reporting for owners, administrators,
coordinators, finance staff, tutors, students and guardians. It is built as a
FastAPI modular monolith with a React single-page client, backed by PostgreSQL.

## Status

**Pre-alpha.** Only the local development foundation exists so far: a FastAPI
API with liveness and readiness health checks, backed by a local PostgreSQL
container, and a React + TypeScript + Vite placeholder page that shows whether
the API is live. No product features are implemented yet.

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

`make dev` builds the images and starts PostgreSQL, the API and the web app in
the background, and returns once all three are healthy. Then open:

- Web app: <http://127.0.0.1:5173> (shows `API: live` when the API is up)
- Liveness: <http://127.0.0.1:8000/health/live>
- Readiness (includes PostgreSQL): <http://127.0.0.1:8000/health/ready>

The web app calls the API on its own origin: the Vite dev server proxies
`/health` and `/api` to the API. The Compose web service has no hot reload; for
that, run `corepack pnpm --filter @classora/web dev` on the host, which proxies
to `http://127.0.0.1:8000`.

Stop the stack with `docker compose down` (data is kept in a named volume;
`docker compose down -v` deletes it).

## Configuration

`.env.example` documents every variable with safe local placeholders; copy it
to `.env` as shown above. `.env` is git-ignored and must never be committed.
The API validates its configuration when it starts and refuses to start on a
missing or invalid value; `docker compose logs api` shows a short
`Configuration error` message naming each bad variable. If your `.env` was
copied before a variable was added, refresh it from `.env.example` (for
example, copy it again).

## Running tests

Docker must be running: API integration tests start a throwaway PostgreSQL
container with [Testcontainers](https://testcontainers.com/). The Compose
stack and `.env` are not needed.

```bash
make test              # all tests: API (unit + integration) then web (Vitest)
make test-api          # all API tests with a coverage report
make test-web          # web component tests (Vitest, no Docker needed)
make test-unit         # API unit tests only (no Docker needed)
make test-integration  # API integration tests only
```

Each API run prints a terminal coverage report for the API (`app/`), with
branch coverage and missing lines. `make test` does not start the Compose database,
and it can run while `make dev` is up.

## Linting and type checks

```bash
make lint      # both apps
make lint-api  # Ruff (lint and format check) and mypy (strict) on the API
make lint-web  # ESLint, Prettier check and TypeScript (strict) on the web app
```

Lint targets only check and never modify files. The web targets install
dependencies from the committed lockfile first, so no manual `pnpm install` is
needed. `make lint` and `make test` fail if either app fails; use `make -k` to
run both parts regardless.

To build the web app: `corepack pnpm --filter @classora/web build` (output in
`apps/web/dist/`).

## Documentation

- [Product specification](_docs/spec.md)
- [Architecture](_docs/architecture.md)
- [Agent instructions](AGENTS.md)
- [Delivery process](_docs/process.md) (agent workflow and contribution guidance)
