.PHONY: dev migrate seed bootstrap-owner reset-owner-password seed-demo lint lint-api lint-web lint-e2e check-pdf-image test test-api test-web test-unit test-integration test-billing-invariants test-migrations build-web storybook test-storybook e2e api-client check-api-client security audit-deps audit-api audit-web scan-secrets scan-secrets-diff sast scan-image

# pnpm runs through Corepack (pinned by packageManager in package.json), never
# a global install, and never stops for a download prompt.
PNPM := COREPACK_ENABLE_DOWNLOAD_PROMPT=0 corepack pnpm

# Security scanners (_docs/adr/0016-ci-security-tooling.md). Each version is pinned
# here and nowhere else; images by multi-arch index digest (the tag is informational).
# Never pin Trivy v0.69.4 (backdoored release, March 2026; see ADR-0016).
PIP_AUDIT_VERSION := 2.10.1
GITLEAKS_IMAGE := zricethezav/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f
SEMGREP_IMAGE := semgrep/semgrep:1.179.0@sha256:93963d9295a366f59e4850127b1550400ee7b388f04fe144e4a1f6325d96e01b
TRIVY_IMAGE := aquasec/trivy:0.75.0@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa

# The repository and its git directory are mounted read-only at their host paths, so
# git inside the scanner containers also works in a git worktree (whose .git is a file).
GIT_COMMON_DIR = $(abspath $(shell git rev-parse --git-common-dir))
REPO_MOUNT = -v "$(CURDIR):$(CURDIR):ro" -v "$(GIT_COMMON_DIR):$(GIT_COMMON_DIR):ro" -w "$(CURDIR)"

GITLEAKS = docker run --rm $(REPO_MOUNT) $(GITLEAKS_IMAGE) \
	git --config "$(CURDIR)/.gitleaks.toml" --redact --verbose --no-banner --exit-code 1

# Semgrep registry rule packs (fetched anonymously; --metrics=off sends no telemetry),
# Classora's own rules, and the source trees it scans (exclusions in .semgrepignore).
SEMGREP_CONFIGS := --config p/python --config p/javascript --config p/typescript --config p/react \
	--config .semgrep/classora.yml
SEMGREP_TARGETS := apps/api/app apps/api/migrations apps/web/src packages
SEMGREP = docker run --rm $(REPO_MOUNT) $(SEMGREP_IMAGE) \
	semgrep scan --metrics=off --disable-version-check --no-git-ignore $(SEMGREP_CONFIGS) $(SEMGREP_TARGETS)

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

## Create the first Owner account: make bootstrap-owner USERNAME=<name> (prompts for the
## password twice; refused once any Owner exists). Run `make migrate` and `make seed` first.
# Same image and direct connection as `make seed` (ADR-0003); runs in every APP_ENV.
bootstrap-owner:
	@test -n "$(USERNAME)" || { echo "usage: make bootstrap-owner USERNAME=<name>" >&2; exit 2; }
	docker compose run --rm --build api python -m app.cli bootstrap-owner --username "$(USERNAME)"

## Print a one-time password-reset link for a locked-out active Owner:
## make reset-owner-password USERNAME=<name> (shown once; expires in 24 hours).
# Same image and direct connection as `make bootstrap-owner` (ADR-0003); runs in every APP_ENV.
reset-owner-password:
	@test -n "$(USERNAME)" || { echo "usage: make reset-owner-password USERNAME=<name>" >&2; exit 2; }
	docker compose run --rm --build api python -m app.cli reset-owner-password --username "$(USERNAME)"

## Generate synthetic es_MX demo data on the host (refused unless APP_ENV is local or dev; needs .env).
# Runs with the API dev dependencies (Factory Boy, Faker), which the image does not contain.
seed-demo:
	cd apps/api && uv run --env-file ../../.env python -m tests.factories.demo

## Lint both apps and the E2E tests (check only, never modifies files). Fails if any part fails.
lint: lint-api lint-web lint-e2e

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

## Install from the lockfile, then tsc (strict) and the Prettier check on the E2E tests (e2e/).
lint-e2e:
	$(PNPM) install --frozen-lockfile
	$(PNPM) --filter @classora/e2e typecheck
	$(PNPM) --filter @classora/e2e format:check

## Run all tests for both apps (not E2E). Fails if either part fails.
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

## Run the API integration tests except the migration checks (those are `make test-migrations`)
## and the billing invariant suite (`make test-billing-invariants`)
## (Testcontainers PostgreSQL; Docker must be running). SHARD=K/N runs only shard K of N, as in CI.
test-integration:
	cd apps/api && uv run pytest -m integration --ignore=tests/integration/test_migrations.py --ignore=tests/integration/billing_invariants $(if $(SHARD),--shard=$(SHARD))

## Run the billing invariant suite (randomized sequences, races, job walks; Docker; no coverage).
## SEEDS=5,9 replaces the default seeds, EXTRA_SEED=n adds one, STEPS=n truncates every sequence:
## a failure prints the exact `make test-billing-invariants SEEDS=… STEPS=…` that reproduces it.
test-billing-invariants:
	cd apps/api && $(if $(SEEDS),BILLING_INVARIANT_SEEDS=$(SEEDS)) $(if $(STEPS),BILLING_INVARIANT_STEPS=$(STEPS)) $(if $(EXTRA_SEED),BILLING_INVARIANT_EXTRA_SEED=$(EXTRA_SEED)) uv run pytest tests/integration/billing_invariants --no-cov --durations=10

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

## Build the API image and render the sample PDF in es and en inside it (ADR-0015).
## Fails on a render error or a missing system library (warnings are errors). Needs Docker.
check-pdf-image:
	docker build -f infrastructure/docker/api.Dockerfile -t classora-api:pdf-check .
	docker run --rm classora-api:pdf-check python -m app.cli pdf-smoke

## Playwright E2E (smoke and axe) against an isolated, freshly migrated and seeded Compose
## stack (project classora-e2e, own ports and volume; always torn down, E2E_KEEP=1 keeps it).
## Needs Docker, uv and Node 24, not .env; runs while `make dev` is up (e2e/scripts/run.sh).
e2e:
	$(PNPM) install --frozen-lockfile
	e2e/scripts/run.sh

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

## Run the PR security scans: dependency audit, secrets (full history) and SAST.
## Fails if any fails. Needs Docker, uv and Node 24 (ADR-0016). Not the image scan.
security:
	@fail=0; \
	$(MAKE) --no-print-directory audit-deps || fail=1; \
	$(MAKE) --no-print-directory scan-secrets || fail=1; \
	$(MAKE) --no-print-directory sast || fail=1; \
	exit $$fail

## Audit the Python and npm lockfiles for known vulnerabilities. Fails if either fails.
audit-deps:
	@fail=0; \
	$(MAKE) --no-print-directory audit-api || fail=1; \
	$(MAKE) --no-print-directory audit-web || fail=1; \
	exit $$fail

## pip-audit over apps/api/uv.lock (all groups, hashes required, nothing installed).
## Fails on any known vulnerability not in apps/api/pip-audit-ignore.txt.
audit-api:
	@set -eu; \
	ids=$$(infrastructure/scripts/check-scan-exceptions.sh pip apps/api/pip-audit-ignore.txt); \
	req=$$(mktemp); trap 'rm -f "$$req"' EXIT; \
	cd apps/api; \
	uv export --frozen --all-groups --no-emit-project --format requirements-txt --quiet --output-file "$$req" >/dev/null; \
	echo "pip-audit $(PIP_AUDIT_VERSION): $$(grep -cE '^[A-Za-z0-9]' "$$req") pinned packages from apps/api/uv.lock"; \
	uvx --quiet pip-audit==$(PIP_AUDIT_VERSION) --requirement "$$req" --require-hashes --disable-pip \
	  --progress-spinner off --desc off --aliases on $$(for id in $$ids; do printf -- '--ignore-vuln %s ' "$$id"; done)

## pnpm audit over pnpm-lock.yaml (every workspace package, prod and dev; nothing installed).
## Fails on a high or critical advisory not in auditConfig.ignoreGhsas (pnpm-workspace.yaml).
audit-web:
	@infrastructure/scripts/check-scan-exceptions.sh pnpm pnpm-workspace.yaml
	$(PNPM) audit --audit-level high

## gitleaks over the full git history (all refs, redacted). Fails on any finding.
scan-secrets:
	$(GITLEAKS)

## gitleaks over the commits in BASE..HEAD only, e.g. a pull request (redacted).
scan-secrets-diff:
	@test -n "$(BASE)" -a -n "$(HEAD)" || { \
	  echo "usage: make scan-secrets-diff BASE=<commit> HEAD=<commit>" >&2; exit 2; }
	$(GITLEAKS) --log-opts="$(BASE)..$(HEAD)"

## Semgrep CE over the API, migrations, web source and packages, in two passes:
## INFO and WARNING findings are printed only; any ERROR finding fails the target.
sast:
	$(SEMGREP) --severity INFO --severity WARNING
	$(SEMGREP) --severity ERROR --error

## Trivy vulnerability scan of a local or registry image: make scan-image IMAGE=<ref>.
## Prints HIGH and CRITICAL; fails on CRITICAL with a fixed version (not in .trivyignore).
## Not run in PR CI; M9-06 runs it after docker build, before push (ADR-0016).
scan-image:
	@test -n "$(IMAGE)" || { echo "usage: make scan-image IMAGE=<image reference>" >&2; exit 2; }
	@infrastructure/scripts/check-scan-exceptions.sh trivy .trivyignore
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock:ro \
	  -v classora-trivy-cache:/root/.cache -v "$(CURDIR)/.trivyignore:/trivyignore:ro" \
	  $(TRIVY_IMAGE) image --quiet --scanners vuln --ignorefile /trivyignore --timeout 15m \
	  --table-mode detailed --severity HIGH,CRITICAL --exit-code 0 "$(IMAGE)"
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock:ro \
	  -v classora-trivy-cache:/root/.cache -v "$(CURDIR)/.trivyignore:/trivyignore:ro" \
	  $(TRIVY_IMAGE) image --quiet --scanners vuln --ignorefile /trivyignore --skip-db-update \
	  --table-mode detailed --severity CRITICAL --ignore-unfixed --exit-code 1 "$(IMAGE)"

# --- Native local stack (agents and developers; _docs/process.md) ------------
# One shared PostgreSQL container (Compose project classora-shared, 127.0.0.1:55432)
# with one database per worktree or clone; the API, worker and web app run on the
# host (no image builds, no PgBouncer). The Docker stack (make dev, make e2e) and
# CI stay the reference. Settings: this checkout's .env (ENV_FILE=<path> picks
# another), and variables set in the shell or on the make command line win over it.
.PHONY: db-shared db-shared-down db-create db-drop api-native worker-native web-native

SHARED_DB_COMPOSE := docker compose -f infrastructure/docker/compose.shared-db.yaml
WITH_ENV := infrastructure/scripts/with-env.sh

## Start the shared PostgreSQL server for native stacks (idempotent; 127.0.0.1:55432).
db-shared:
	$(SHARED_DB_COMPOSE) up -d --wait

## Stop the shared PostgreSQL server; its volume (every database) is kept unless V=1.
db-shared-down:
	$(SHARED_DB_COMPOSE) down $(if $(filter 1,$(V)),-v)

## Create a database and its role on the shared server: make db-create NAME=<name>
## (name: [a-z0-9_]+; idempotent). Prints the DATABASE_URL and DATABASE_DIRECT_URL to set.
db-create:
	@infrastructure/scripts/shared-db.sh create "$(NAME)"

## Drop a database and its role from the shared server: make db-drop NAME=<name>.
db-drop:
	@infrastructure/scripts/shared-db.sh drop "$(NAME)"

## Apply the migrations, then run the API on the host with reload on 127.0.0.1:API_PORT
## (default 8000) against DATABASE_URL / DATABASE_DIRECT_URL.
api-native:
	$(WITH_ENV) sh -c 'cd apps/api && uv run alembic upgrade head && \
	  exec uv run uvicorn --factory app.main:create_app --host 127.0.0.1 --port "$${API_PORT:-8000}" \
	  --reload --reload-dir app --no-access-log --no-proxy-headers --timeout-graceful-shutdown 5'

## Run the background worker on the host against DATABASE_DIRECT_URL.
worker-native:
	$(WITH_ENV) sh -c 'cd apps/api && exec uv run python -m app.worker'

## Install from the lockfile, then run the Vite dev server (hot reload) on 127.0.0.1:WEB_PORT
## (default 5173), proxying to API_PROXY_TARGET (default http://127.0.0.1:API_PORT).
web-native:
	$(PNPM) install --frozen-lockfile
	COREPACK_ENABLE_DOWNLOAD_PROMPT=0 $(WITH_ENV) sh -c \
	  'export API_PROXY_TARGET="$${API_PROXY_TARGET:-http://127.0.0.1:$${API_PORT:-8000}}"; \
	  exec corepack pnpm --filter @classora/web exec vite --port "$${WEB_PORT:-5173}"'
