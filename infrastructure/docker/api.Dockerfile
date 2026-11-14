# Image for the FastAPI service and the background worker (the `worker`
# service runs this same image with another command): the same image locally
# (`make dev`, `make e2e`), in CI and, built for linux/arm64, on AWS (ADR-0010).
# Build context: repository root.
# The Python 3.14 pin is set by _docs/adr/0001-runtime-version-pins.md (accepted).
# Keep in sync with apps/api/.python-version and apps/api/pyproject.toml.
#
# Stages (#672 D1): `base` (system libraries, user, uv), `test` (the API
# test and tools image: dev dependencies and the whole repository; only for
# `make test` and `make seed-data-*`, never pushed) and the runtime image as
# the LAST stage, so a build without --target (make dev, make build-images,
# make check-images, make check-pdf-image, AWS) produces it, unchanged.
FROM python:3.14-slim AS base

# libpq for psycopg (used only by Procrastinate, ADR-0003): Debian's runtime
# library, OS-patched and visible to image scanners. The psycopg[binary] wheel
# is a dev-only dependency, so the --no-dev install below does not contain it.
# Pango, HarfBuzz subsetting and DejaVu fonts for WeasyPrint's server-rendered
# PDFs (_docs/adr/0015-server-side-i18n.md); the worker shares this image, so
# it renders PDFs too. `make check-pdf-image` checks them. Keep the list equal
# to the CI apt step in .github/workflows/ci.yml.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# The base image's pip is never used (uv installs everything) and vendors its
# own certifi and msgpack, which the image scan flags (#610): removed.
RUN python -m pip uninstall --yes --quiet pip

# Runs as a non-root system user, uid/gid 10001 (#610 E2 (a)), everywhere.
# HOME is a writable directory for fontconfig's cache (WeasyPrint). The
# application files below stay root-owned, so the process cannot change them.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --no-log-init --create-home \
       --home-dir /home/app --shell /usr/sbin/nologin app

COPY --from=ghcr.io/astral-sh/uv:0.9.13 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

# Test and tools image (#672 D1): `make test` runs pytest in it (Testcontainers
# through the host's Docker socket) and `make seed-data-*` runs the simulated
# seed in it (Compose `devtools`). Dev dependencies in /opt/venv, outside the
# copied repository; the repository at /repo, at its real layout, because the
# unit tests read Makefile, ci.yml, the docs and infrastructure/. Runs as
# root: Docker Desktop's socket inside a container is root-only.
FROM base AS test

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /repo/apps/api

COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/.python-version ./
RUN uv sync --frozen --no-install-project

COPY . /repo

ENV PATH="/opt/venv/bin:$PATH"

# Bytecode for the API, its migrations and the tests (#672 QA D1): the copied
# sources carry no .pyc (.dockerignore), and several tests start the API in a
# subprocess with a fixed readiness window; compiling there on a busy machine
# took most of it.
RUN python -m compileall -q -j 0 app migrations tests

# Runtime image (the default build): the API, the worker and `migrate`.
FROM base

WORKDIR /app

COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY apps/api/app ./app
# Alembic runs from this image (`make migrate`); the API never migrates itself.
COPY apps/api/alembic.ini ./
COPY apps/api/migrations ./migrations

ENV PATH="/app/.venv/bin:$PATH" \
    HOME=/home/app

# Numeric, so a runtime can check "not root" without reading /etc/passwd.
USER 10001:10001

EXPOSE 8000

# The worker service overrides this with `python -m app.worker`.
# --no-access-log: app.access writes one JSON access line per request instead.
# --no-proxy-headers: uvicorn never rewrites the client address from
# X-Forwarded-For; only the app reads it, from TRUSTED_PROXIES (#27, ADR-0008).
# --timeout-graceful-shutdown 5: open live-notification streams (#42) never
# finish on their own, so on stop uvicorn cancels them after 5 s and then runs
# the lifespan shutdown, well within Docker's 10 s stop timeout.
# --no-server-header: no "server: uvicorn" product name (#150 SR-03; the edge
# also drops any Server header).
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers", "--no-server-header", "--timeout-graceful-shutdown", "5"]
