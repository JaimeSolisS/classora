.PHONY: dev test

# Export variables from .env (DATABASE_URL etc.) to recipes, if present.
ifneq (,$(wildcard .env))
include .env
export
endif

## Build the API image and start db + api in the background (idempotent).
dev:
	docker compose up -d --build --wait

## Ensure the db service is healthy, then run the API test suite on the host.
test:
	docker compose up -d --wait db
	cd apps/api && uv run pytest
