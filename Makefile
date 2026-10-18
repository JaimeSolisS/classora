.PHONY: dev migrate seed seed-demo lint lint-api lint-web test test-api test-web test-unit test-integration test-migrations build-web storybook test-storybook api-client check-api-client

# pnpm runs through Corepack (pinned by packageManager in package.json), never
# a global install, and never stops for a download prompt.
PNPM := COREPACK_ENABLE_DOWNLOAD_PROMPT=0 corepack pnpm

## Build the images and start db, pgbouncer, api, worker and web in the background (idempotent).
# The worker (same image as api) waits for its tables until `make migrate` has run.
dev:
	docker compose up -d --build --wait

## Apply Alembic migrations to the local Compose database (starts db if needed; make dev never migrates).
# --build: uses the migrations in the working tree. Alembic connects directly to PostgreSQL (ADR-0003).
migrate:
	docker compose run --rm --build api alembic upgrade head

## Apply the reference seeds to the local Compose database (idempotent; run `make migrate` first).
# Same image and direct connection as `make migrate` (ADR-0003); never migrates.
seed:
	docker compose run --rm --build api python -m app.seeds

## Generate synthetic es_MX demo data on the host (refused unless APP_ENV is local or dev; needs .env).
# Runs with the API dev dependencies (Factory Boy, Faker), which the image does not contain.
seed-demo:
	cd apps/api && uv run --env-file ../../.env python -m tests.factories.demo

## Lint both apps (check only, never modifies files). Fails if either part fails.
lint: lint-api lint-web

## Lint, format-check and type-check the API.
lint-api:
	cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy app

## Install from the lockfile, then ESLint, Prettier check and tsc for the web app
## and the API client package (strict, including its compile-time type checks).
## `pnpm install` fails on a Node major other than engines.node (engineStrict).
lint-web:
	$(PNPM) install --frozen-lockfile
	$(PNPM) --filter @classora/web lint
	$(PNPM) --filter @classora/web format:check
	$(PNPM) --filter @classora/web typecheck
	$(PNPM) --filter @classora/api-client lint
	$(PNPM) --filter @classora/api-client format:check
	$(PNPM) --filter @classora/api-client typecheck

## Run all tests for both apps. Fails if either part fails.
test: test-api test-web

## Run all API tests (unit + integration) with one coverage report.
## Integration tests start a throwaway Testcontainers PostgreSQL; Docker must be running.
test-api:
	cd apps/api && uv run pytest

## Install from the lockfile, then run the web and API client Vitest suites once (no watch mode).
test-web:
	$(PNPM) install --frozen-lockfile
	$(PNPM) --filter @classora/web test
	$(PNPM) --filter @classora/api-client test

## Run only the API unit tests (no Docker needed).
test-unit:
	cd apps/api && uv run pytest -m unit

## Run only the API integration tests (Testcontainers PostgreSQL; Docker must be running).
test-integration:
	cd apps/api && uv run pytest -m integration

## Run only the migration tests: upgrade from empty, single head, round trip, no model drift (Docker).
test-migrations:
	cd apps/api && uv run pytest tests/integration/test_migrations.py

## Install from the lockfile, then build the web app for production (output in apps/web/dist/).
build-web:
	$(PNPM) install --frozen-lockfile
	$(PNPM) --filter @classora/web build

## Install from the lockfile, then run Storybook on http://127.0.0.1:6006 (host only,
## no browser is opened). Fails instead of moving to another port when 6006 is taken.
storybook:
	$(PNPM) install --frozen-lockfile
	$(PNPM) --filter @classora/web storybook

## Install from the lockfile and the headless Chromium (downloaded on first run only),
## then run every story with its play function and the axe check, in light and dark.
test-storybook:
	$(PNPM) install --frozen-lockfile
	$(PNPM) --filter @classora/web exec playwright install --only-shell chromium
	$(PNPM) --filter @classora/web test-storybook

## Regenerate the API contract: export the OpenAPI document, then the TypeScript client types.
## Run after any API change and commit the result. Needs uv and Node 24; no Docker, database or .env.
api-client:
	$(PNPM) install --frozen-lockfile
	cd apps/api && uv run python -m app.core.openapi ../../packages/api-client/openapi.json
	$(PNPM) --filter @classora/api-client generate

## Drift check (CI): regenerate, then fail if packages/api-client differs from the commit
## (changed, deleted or new untracked files).
check-api-client: api-client
	@git diff --exit-code HEAD -- packages/api-client && \
	  test -z "$$(git status --porcelain --untracked-files=all -- packages/api-client)" || { \
	    git status --short --untracked-files=all -- packages/api-client; \
	    echo "check-api-client: packages/api-client is out of date with the API." >&2; \
	    echo "Run 'make api-client' and commit the result." >&2; \
	    exit 1; }
	@echo "check-api-client: packages/api-client is in sync with the API."
