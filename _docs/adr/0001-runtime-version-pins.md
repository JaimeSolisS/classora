# ADR-0001: Runtime version pins

## Status

Accepted

## Date

2026-10-17

## Sign-off

Owner: JaimeSolisS. Approved as proposed on 2026-10-17 (GitHub issue #6, orchestrator decision record).

Amended 2026-11-11: observability images (issue #611, M9-03, escalation E1 option (a), owner-delegated recommended decision; see [Amendments](#amendments)).

Amended 2026-11-12: PostgreSQL image digest on the AWS hosts (issue #632, from QA #614 O4, owner-delegated recommended decision; see [Amendments](#amendments)).

## Context

Resolves architecture §10 **ISSUE-D7** (version pins) in [`../architecture.md`](../architecture.md) §10.3. The spec ([`../spec.md`](../spec.md) §10, stack table) names the technologies but pins no major versions.

Milestone 1 issues #1, #3 and #4 set these pins as provisional, pending this decision gate. This ADR ratifies the values actually in the repository.

- **PostgreSQL 18** has native `uuidv7()`, which gives index-friendly UUID primary keys (spec §10 "UUID primary keys"; architecture ISSUE-D7).
- **Python 3.14** is the backend runtime used by the API image, uv, Ruff and mypy.
- **Node 24** is the active LTS line and still bundles Corepack, which runs the pinned pnpm. Later Node majors no longer bundle Corepack.
- **pnpm 12.x** is the workspace package manager, run through Corepack.
- **React 19, Vite 8, TypeScript 6.0** are the frontend majors. TypeScript is held at 6.0.x, below 7, because `typescript-eslint` supports only `typescript <6.1`.

## Decision

The runtime and frontend pins are:

| Component | Pin | Where it is pinned |
|---|---|---|
| Python | 3.14 (minor; the patch level comes from the image and uv) | `apps/api/.python-version`, `requires-python = "==3.14.*"` in `apps/api/pyproject.toml`, `FROM python:3.14-slim` in `infrastructure/docker/api.Dockerfile`, Ruff `target-version = "py314"` and mypy `python_version = "3.14"` in `apps/api/pyproject.toml` |
| PostgreSQL | 18 (major tag `postgres:18`) | `image: postgres:18` in `compose.yaml`, and `POSTGRES_IMAGE = "postgres:18"` in `apps/api/tests/integration/conftest.py` |
| Node.js | 24 LTS (major) | `.nvmrc` (`24`), `engines.node ">=24 <25"` in the root `package.json`, `FROM node:24-slim` in `infrastructure/docker/web.Dockerfile` |
| pnpm | 12.x, exact `pnpm@12.14.0`, run through Corepack | `packageManager` in the root `package.json` |
| React / react-dom | 19 (19.3.1 at the time of this ADR) | `apps/web/package.json` and `pnpm-lock.yaml` |
| Vite | 8 (8.3.4) | `apps/web/package.json` and `pnpm-lock.yaml` |
| TypeScript | 6.0.x (6.0.3), held below 7 because `typescript-eslint` supports only `typescript <6.1` | `apps/web/package.json` and `pnpm-lock.yaml` |
| Vitest, ESLint, Prettier | 5, 10, 3 (5.0.5, 10.12.0, 3.9.10), approved as tooling majors on #4 | `apps/web/package.json` and `pnpm-lock.yaml` |
| Procrastinate, psycopg | The versions recorded in [ADR-0003](0003-database-connection-topology.md) §Verification. They are added to the lock in M1-16. | not yet in the repo |

**Pin policy.**

- Python is pinned at the **minor** level. PostgreSQL and Node are pinned at the **major** level. pnpm is pinned to an **exact** version.
- Library versions are exact in the lockfiles (`apps/api/uv.lock`, `pnpm-lock.yaml`). This ADR governs only their majors.
- Changing any pin in the table above at the major level (minor level for Python) requires a new or superseding ADR.
- Patch and minor bumps within a pinned major are ordinary lockfile changes.

## Amendments

### A1. Observability images (2026-11-11, #611)

The monitoring stack ([ADR-0014](0014-observability-error-tracking-and-alerting.md)) runs these images, locally (`make dev-observability`) and on AWS. Each is pinned by an **exact tag and its multi-arch index digest**, as the Makefile pins the scanners ([ADR-0016](0016-ci-security-tooling.md)). Each index has `linux/amd64` and `linux/arm64`. Each version was released at least two weeks before the pin.

| Image | Tag | Index digest | Released |
|---|---|---|---|
| `otel/opentelemetry-collector-contrib` | `0.164.0` | `sha256:340c9ca6a36617084c6661bdc4f0fe92b7cda5a805d61673ec8db375fddc522b` | 2026-10-27 |
| `prom/prometheus` (3.x, native OTLP receiver) | `v3.15.1` | `sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2` | 2026-10-21 |
| `grafana/loki` (3.x) | `3.7.9` | `sha256:0db216b630ae1f348a1584c67f3cead8b4c1fc839169a39501db664744158e28` | 2026-10-20 |
| `grafana/tempo` (2.x) | `2.10.10` | `sha256:12b0820f13ffa10ce956b6e9acc3b03f3cffded99109be6cc09d106fe56e5d38` | 2026-10-21 |
| `grafana/grafana` (12.x) | `12.4.14` | `sha256:ae509d9ae8c1021ca32ec370a9ca975d40165eda9fccd1e1102d317ff8787cd7` | 2026-10-27 |

- The pins live in one file, `infrastructure/observability/compose.observability.yaml`. `make check-observability` validates each configuration with the image pinned there, and fails if an image isn't pinned by tag and digest.
- The majors (Prometheus 3, Loki 3, Tempo 2, Grafana 12) are governed like the pins in the table above: changing one needs an amendment. Patch and minor bumps within a major, and Collector releases, are ordinary changes that update the tag, the digest and this table together.

### A2. PostgreSQL on the AWS hosts (2026-11-12, #632)

On the AWS app hosts (DEV, PROD), `db` runs PostgreSQL pinned by **exact minor tag and multi-arch index digest**, so a replaced instance or a later deploy gets the same image as the one tested, not whatever `postgres:18` points to that day.

| Image | Tag | Index digest | Released |
|---|---|---|---|
| `postgres` | `18.6` | `sha256:e3d24f6906d0760bf4da0dae872415391ededefad23fd471c47dc53b0ddf3c42` | 18.6: 2026-08-13; this build: 2026-11-06 |

- The index has `linux/arm64` (the hosts) and `linux/amd64`. It is the digest DEV was already running, so the pin did not change DEV's database image.
- The pin lives in one file, `infrastructure/host/compose.aws.yaml` (service `db`). `restore.sh` uses the live `db` image, or this pin when no `db` container exists.
- The governed level is unchanged: PostgreSQL stays pinned at the **major** (18) in the table above. Local Compose, the shared native database and Testcontainers keep `postgres:18`.
- Minor and rebuild bumps within 18 are ordinary changes that update the tag, the digest and this table together. They take effect on the next deploy (`deploy.sh` pulls and recreates `db`). A new major needs a superseding ADR and a data migration (`pg_upgrade` or dump/restore).

## Consequences

- The files in the table above are the "equivalent files" that ISSUE-D7 allows. No `.tool-versions` file is added.
- `.nvmrc` holds a bare `24` and cannot carry a comment: nvm fails to read the file when a comment is present. The link between `.nvmrc`, `engines.node` and the web Dockerfile is noted in the root `package.json` `"//"` field and in `web.Dockerfile`. (Accepted deviation from #4.)
- ESLint is held at a release that pnpm 12's minimum-release-age policy allows, so it is not always the newest 10.x release. (Accepted deviation from #4.)
- Each pin lives in several files. A change at the governed level must update every file in its row together.
- Developer machines need Node 24 (for example through nvm) and Python 3.14 (through uv).

## Alternatives considered

- **A single `.tool-versions` file.** Not added. The per-tool files above are already read by the tools that need them (uv, nvm, Corepack, Docker, Ruff, mypy, Testcontainers), and ISSUE-D7 accepts equivalent files.
- **TypeScript 7.** Rejected for now because `typescript-eslint` supports only `typescript <6.1`.
- **A Node major later than 24.** Rejected: it is not the active LTS and no longer bundles Corepack.

## References

- [`../architecture.md`](../architecture.md) §10.3 ISSUE-D7, §11
- [`../spec.md`](../spec.md) §10 (stack table: keys/time)
- GitHub issues #1 (Python, PostgreSQL), #3 (Ruff/mypy targets, Testcontainers image), #4 (Node, pnpm, frontend majors, ESLint release-age deviation, `.nvmrc`), #6 (this decision gate)
