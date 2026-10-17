.PHONY: dev lint test test-unit test-integration

## Build the API image and start db + api in the background (idempotent).
dev:
	docker compose up -d --build --wait

## Lint, format-check and type-check the API (check only, never modifies files).
lint:
	cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy app

## Run all API tests (unit + integration) with one coverage report.
## Integration tests start a throwaway Testcontainers PostgreSQL; Docker must be running.
test:
	cd apps/api && uv run pytest

## Run only the unit tests (no Docker needed).
test-unit:
	cd apps/api && uv run pytest -m unit

## Run only the integration tests (Testcontainers PostgreSQL; Docker must be running).
test-integration:
	cd apps/api && uv run pytest -m integration
