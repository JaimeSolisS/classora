# Local development image for the FastAPI service. Build context: repository root.
# The Python 3.14 pin is set by _docs/adr/0001-runtime-version-pins.md (accepted).
# Keep in sync with apps/api/.python-version and apps/api/pyproject.toml.
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.13 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY apps/api/app ./app

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# --no-access-log: app.access writes one JSON access line per request instead.
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
