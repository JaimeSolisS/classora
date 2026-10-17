# Classora

Classora is a management platform for a single tutoring center. It covers class
scheduling and enrollment, attendance, academic records, documents, billing and
payments, tutor payroll and operational reporting for owners, administrators,
coordinators, finance staff, tutors, students and guardians. It is built as a
FastAPI modular monolith with a React single-page client, backed by PostgreSQL.

## Status

**Pre-alpha.** Only the local development foundation exists so far: a FastAPI
API with liveness and readiness health checks, backed by a local PostgreSQL
container. No product features are implemented yet.

## Prerequisites

- [Docker Engine](https://docs.docker.com/engine/) with Docker Compose v2
- GNU Make
- [uv](https://docs.astral.sh/uv/) (installs the pinned Python version; no other Python install is needed)

## Getting started

```bash
cp .env.example .env
make dev
```

`make dev` builds the API image and starts PostgreSQL and the API in the
background. Then check:

- Liveness: <http://127.0.0.1:8000/health/live>
- Readiness (includes PostgreSQL): <http://127.0.0.1:8000/health/ready>

Stop the stack with `docker compose down` (data is kept in a named volume;
`docker compose down -v` deletes it).

## Running tests

```bash
make test
```

`make test` starts the PostgreSQL container if needed and runs the API test
suite with pytest through uv.

## Documentation

- [Product specification](_docs/spec.md)
- [Architecture](_docs/architecture.md)
- [Agent instructions](AGENTS.md)
- [Delivery process](_docs/process.md) (agent workflow and contribution guidance)
