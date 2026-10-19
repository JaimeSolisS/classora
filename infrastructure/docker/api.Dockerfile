# Local development image for the FastAPI service and the background worker
# (the `worker` service runs this same image with another command). Build
# context: repository root.
# The Python 3.14 pin is set by _docs/adr/0001-runtime-version-pins.md (accepted).
# Keep in sync with apps/api/.python-version and apps/api/pyproject.toml.
FROM python:3.14-slim

# libpq for psycopg (used only by Procrastinate, ADR-0003): Debian's runtime
# library, OS-patched and visible to image scanners. The psycopg[binary] wheel
# is a dev-only dependency, so the --no-dev install below does not contain it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.13 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY apps/api/app ./app
# Alembic runs from this image (`make migrate`); the API never migrates itself.
COPY apps/api/alembic.ini ./
COPY apps/api/migrations ./migrations

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# The worker service overrides this with `python -m app.worker`.
# --no-access-log: app.access writes one JSON access line per request instead.
# --no-proxy-headers: uvicorn never rewrites the client address from
# X-Forwarded-For; only the app reads it, from TRUSTED_PROXIES (#27, ADR-0008).
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
