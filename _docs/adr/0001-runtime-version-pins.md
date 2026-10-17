# ADR-0001: Runtime version pins

## Status

Accepted

## Date

2026-10-17

## Sign-off

Owner: JaimeSolisS. Approved as proposed on 2026-10-17 (GitHub issue #6, orchestrator decision record).

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
