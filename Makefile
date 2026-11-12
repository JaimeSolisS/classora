.PHONY: dev migrate seed bootstrap-owner reset-owner-password seed-data-spanish seed-data-english lint lint-api lint-web lint-e2e check-pdf-image test test-api test-web test-unit test-integration test-billing-invariants test-simulation test-migrations build-web storybook test-storybook e2e api-client check-api-client security audit-deps audit-api audit-web scan-secrets scan-secrets-diff sast scan-image build-images check-images smoke-edge dev-observability dev-observability-down check-observability check-infra tf-plan tf-apply tf-destroy env-teardown monitoring-sync

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
## On a new checkout or volume run `make migrate` first: it creates the database role
## (classora_app) the api, worker and PgBouncer log in as (#165).
dev:
	@docker compose up -d --build --wait || { status=$$?; \
	  echo "If this is a new checkout or volume, run make migrate first (it creates the database role the app uses)." >&2; \
	  exit $$status; }

# Optional local observability (ADR-0014, #611): OTel Collector, Prometheus, Loki,
# Tempo and Grafana, Compose profile `observability` (root compose.yaml). Off by
# default: `make dev` never starts it. Image pins: compose.observability.yaml.
OBS_SERVICES := otel-collector prometheus loki tempo grafana
OBS_VOLUMES := prometheus-data loki-data tempo-data grafana-data

## Start the app stack plus the observability profile (about 700 MB more RAM; a heavy
## command). Grafana on http://127.0.0.1:GRAFANA_PORT (default 3000), login admin and
## GRAFANA_ADMIN_PASSWORD from .env. Stop only the profile with `make dev-observability-down`.
## The API and worker export telemetry to the local collector (OTEL_EXPORTER_OTLP_ENDPOINT,
## #612); a later `make dev` recreates them with telemetry off.
dev-observability:
	@if docker compose --profile observability config | grep -qE '^ +GF_SECURITY_ADMIN_PASSWORD: ("")?$$'; then \
	  echo "dev-observability: set GRAFANA_ADMIN_PASSWORD in .env (see .env.example)" >&2; exit 2; fi
	OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318 docker compose --profile observability up -d --build --wait

## Stop and remove only the observability containers (the app stack keeps running).
## Their data volumes are kept unless V=1.
dev-observability-down:
	docker compose --profile observability rm --stop --force $(OBS_SERVICES)
	@if [ "$(V)" = 1 ]; then \
	  project=$$(docker compose config | sed -n 's/^name: //p' | head -n 1); \
	  for volume in $(OBS_VOLUMES); do docker volume rm --force "$${project}_$$volume"; done; fi

## Validate every observability config with the pinned images, without starting the stack:
## profile off by default, otelcol validate, promtool, loki/tempo verify, Grafana provisioning.
## Needs Docker and uv (CI job `infra`).
check-observability:
	infrastructure/observability/check.sh

# --- Terraform (ADR-0011, #613; _docs/runbooks/terraform.md) -----------------
# check-infra runs Terraform, shellcheck and actionlint from these images (by
# multi-arch index digest; the tag is informational), so none needs a local install.
TERRAFORM_IMAGE := hashicorp/terraform:1.13.4@sha256:eebc943e69008b6d6d986800087164274d8c92d83db8d53fb9baa4ccff309884
SHELLCHECK_IMAGE := koalaman/shellcheck:v0.11.0@sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d
ACTIONLINT_IMAGE := rhysd/actionlint:1.7.8@sha256:96d4a8c87dbbfb3bdd324f8fdc285fc3df5261e2decc619a4dd7e8ee52bbfd46
TF := infrastructure/scripts/terraform.sh
TF_ROOT_CHECK = @test -n "$(ENV)" || { echo "usage: make $@ ENV=<bootstrap|shared|dev|prod|monitoring>" >&2; exit 2; }

## Static infrastructure checks, no AWS credentials (CI job `infra`): terraform fmt -check
## and validate for every root, shellcheck on infrastructure/scripts and infrastructure/host,
## and actionlint on .github/workflows.
check-infra:
	TERRAFORM_IMAGE=$(TERRAFORM_IMAGE) SHELLCHECK_IMAGE=$(SHELLCHECK_IMAGE) ACTIONLINT_IMAGE=$(ACTIONLINT_IMAGE) $(TF) check

## Plan a Terraform root (AWS_PROFILE defaults to jsolisdev): make tf-plan ENV=<root>
## [ARGS='-replace=<address>']. Saves <root>/tfplan; the first bootstrap plan runs on local state.
tf-plan:
	$(TF_ROOT_CHECK)
	$(TF) plan $(ENV) $(ARGS)

## Apply the saved plan after typing the root's name (no auto-approve): make tf-apply ENV=<root>.
tf-apply:
	$(TF_ROOT_CHECK)
	$(TF) apply $(ENV)

## Destroy a root after typing its name: make tf-destroy ENV=<root>. Refuses bootstrap,
## and prod without a verified final dump newer than its last deploy (prod-destroy-guard.sh).
tf-destroy:
	$(TF_ROOT_CHECK)
	$(TF) destroy $(ENV)

## End of the environments' window (runbook _docs/runbooks/teardown.md): final PROD
## dump, then destroy, each step confirmed. make env-teardown MODE=dev-only|all
## (dev-only keeps DEV with monitoring off; MONITORING=colocated keeps it on DEV).
env-teardown:
	@test -n "$(MODE)" || { echo "usage: make env-teardown MODE=dev-only|all" >&2; exit 2; }
	MONITORING=$(or $(MONITORING),off) infrastructure/scripts/env-teardown.sh $(MODE)

## Sync the dedicated monitoring host with HEAD (bundle infrastructure/observability and
## infrastructure/host, upload, SSM classora-monitoring-sync; #617): make monitoring-sync.
monitoring-sync:
	infrastructure/scripts/monitoring-sync.sh

## Apply Alembic migrations to the local Compose database, then give the runtime role
## classora_app its login and password (starts db if needed; make dev never migrates).
# --build: uses the migrations in the working tree. The `migrate` tools service connects
# directly to PostgreSQL as the owner (DATABASE_MIGRATION_URL; ADR-0003 amendment, #165).
migrate:
	docker compose run --rm --build migrate sh -c 'alembic upgrade head && python -m app.cli sync-runtime-role'

## Apply the reference seeds to the local Compose database (idempotent; run `make migrate` first).
# Same tools service and owner connection as `make migrate` (ADR-0003); never migrates.
seed:
	docker compose run --rm --build migrate python -m app.seeds

## Create the first Owner account: make bootstrap-owner USERNAME=<name> (prompts for the
## password twice; refused once any Owner exists). Run `make migrate` and `make seed` first.
# Same image and direct connection as `make seed` (ADR-0003); runs in every APP_ENV.
bootstrap-owner:
	@test -n "$(USERNAME)" || { echo "usage: make bootstrap-owner USERNAME=<name>" >&2; exit 2; }
	docker compose run --rm --build migrate python -m app.cli bootstrap-owner --username "$(USERNAME)"

## Print a one-time password-reset link for a locked-out active Owner:
## make reset-owner-password USERNAME=<name> (shown once; expires in 24 hours).
# Same image and direct connection as `make bootstrap-owner` (ADR-0003); runs in every APP_ENV.
reset-owner-password:
	@test -n "$(USERNAME)" || { echo "usage: make reset-owner-password USERNAME=<name>" >&2; exit 2; }
	docker compose run --rm --build migrate python -m app.cli reset-owner-password --username "$(USERNAME)"

## Generate the simulated operations data in Spanish on the host (APP_ENV=local only; needs a freshly migrated database and .env).
# A deterministic center from 1 Sep to 5 Dec 2026 (history to 18 Nov), through the services; README "Simulated operations data".
seed-data-spanish:
	cd apps/api && uv run --env-file ../../.env python -m tests.factories.simulation --language es

## Generate the simulated operations data in English on the host (APP_ENV=local only; needs its own freshly migrated database).
seed-data-english:
	cd apps/api && uv run --env-file ../../.env python -m tests.factories.simulation --language en

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

## Run the API integration tests except the migration checks (those are `make test-migrations`),
## the billing invariant suite (`make test-billing-invariants`) and the simulation seed tests
## (`make test-simulation`) (Testcontainers PostgreSQL; Docker must be running).
## SHARD=K/N runs only shard K of N, as in CI (SHARD=K/5).
## The ignore glob is quoted so the shell passes it to pytest unexpanded.
test-integration:
	cd apps/api && uv run pytest -m integration --ignore=tests/integration/test_migrations.py --ignore=tests/integration/billing_invariants --ignore-glob='tests/integration/test_simulation_*.py' $(if $(SHARD),--shard=$(SHARD))

## Run the billing invariant suite (randomized sequences, races, job walks; Docker; no coverage).
## SEEDS=5,9 replaces the default seeds, EXTRA_SEED=n adds one, STEPS=n truncates every sequence:
## a failure prints the exact `make test-billing-invariants SEEDS=… STEPS=…` that reproduces it.
test-billing-invariants:
	cd apps/api && $(if $(SEEDS),BILLING_INVARIANT_SEEDS=$(SEEDS)) $(if $(STEPS),BILLING_INVARIANT_STEPS=$(STEPS)) $(if $(EXTRA_SEED),BILLING_INVARIANT_EXTRA_SEED=$(EXTRA_SEED)) uv run pytest tests/integration/billing_invariants --no-cov --durations=10

## Run only the simulated-operations seed tests (tests/integration/test_simulation_*.py; Docker; no coverage).
test-simulation:
	cd apps/api && uv run pytest tests/integration/test_simulation_*.py --no-cov --durations=10

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

# The images that run on AWS (ADR-0010, #610), named after their ECR repositories
# (classora/<name>). PLATFORM is the target platform: linux/amd64 by default (the
# local Intel machines), linux/arm64 in the CI `images` job (native arm64 runner).
PLATFORM ?= linux/amd64
IMAGE_TAG ?= local
API_IMAGE = classora/api:$(IMAGE_TAG)
WEB_IMAGE = classora/web:$(IMAGE_TAG)
PGBOUNCER_IMAGE = classora/pgbouncer:$(IMAGE_TAG)

## Build the three AWS images for PLATFORM (default linux/amd64): api (also the worker),
## web (the edge: Caddy with the built SPA) and pgbouncer. Same Dockerfiles as make dev.
build-images:
	docker build --platform $(PLATFORM) -f infrastructure/docker/api.Dockerfile -t $(API_IMAGE) .
	docker build --platform $(PLATFORM) -f infrastructure/docker/edge.Dockerfile -t $(WEB_IMAGE) .
	docker build --platform $(PLATFORM) -t $(PGBOUNCER_IMAGE) infrastructure/docker/pgbouncer

## Check the images of `make build-images`: architecture, API as uid 10001, no secret in
## any image's Env, no .env file in the build context. Run `make build-images` first.
check-images:
	infrastructure/docker/check-images.sh $(lastword $(subst /, ,$(PLATFORM))) $(API_IMAGE) $(WEB_IMAGE) $(PGBOUNCER_IMAGE)

## Build the edge image and smoke-test it in front of a stub API: SPA fallback, asset
## caching, security headers (against PRODUCTION_HEADERS), /api proxy, X-Forwarded-For,
## unbuffered SSE, /health/* 404, no Server/Via header, the 4 MB body cap and HSTS over HTTPS.
## Needs Docker, curl, jq and Node 24.
smoke-edge:
	docker build --platform $(PLATFORM) -f infrastructure/docker/edge.Dockerfile -t $(WEB_IMAGE) .
	infrastructure/docker/edge/smoke.sh $(WEB_IMAGE)

## Playwright E2E (smoke and axe) against an isolated, freshly migrated and seeded Compose
## stack (project classora-e2e, own ports and volume; always torn down, E2E_KEEP=1 keeps it).
## Needs Docker, uv and Node 24, not .env; runs while `make dev` is up (e2e/scripts/run.sh).
e2e:
	$(PNPM) install --frozen-lockfile
	e2e/scripts/run.sh

## Regenerate the API contract: export the OpenAPI document and the i18n code inventory, then the TypeScript client types.
## Run after any API change and commit the result. Needs uv and Node 24; no Docker, database or .env.
api-client:
	$(PNPM) install --frozen-lockfile
	cd apps/api && uv run python -m app.core.openapi ../../packages/api-client/openapi.json
	cd apps/api && uv run python -m app.core.i18n_inventory ../../packages/api-client/i18n-inventory.json
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
## The CI `images` job runs it on the three arm64 images; M9-07 (#615) runs it before push (ADR-0016).
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
## (name: [a-z0-9_]+; idempotent). Also creates the cluster-wide runtime role classora_app once.
## Prints the DATABASE_URL, DATABASE_DIRECT_URL (runtime role) and DATABASE_MIGRATION_URL (owner) to set.
db-create:
	@infrastructure/scripts/shared-db.sh create "$(NAME)"

## Drop a database and its role from the shared server: make db-drop NAME=<name>
## (classora_app stays: other databases use it).
db-drop:
	@infrastructure/scripts/shared-db.sh drop "$(NAME)"

## Apply the migrations (as the owner, DATABASE_MIGRATION_URL), then run the API on the host
## with reload on 127.0.0.1:API_PORT (default 8000) against DATABASE_URL / DATABASE_DIRECT_URL.
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
