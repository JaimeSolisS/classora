#!/usr/bin/env bash
# `make e2e`: run the Playwright tests against an isolated, freshly seeded
# Compose stack, then tear it down (rules: _docs/testing-guidelines.md, E2E).
#
#   1. remove any leftover classora-e2e project (down -v)
#   2. build and start every service (up -d --build --wait)
#   3. alembic upgrade head      (api image, direct connection; ADR-0003)
#   4. python -m app.seeds       (api image)
#   5. demo command, --seed 1    (host, uv; API dev dependencies)
#   6. headless Chromium         (downloaded on first run only)
#   7. Playwright
#   8. on failure: Compose logs -> e2e/test-results/compose.log
#   9. always: down -v (also after a failure or Ctrl-C), unless E2E_KEEP=1
#
# The stack is its own Compose project (classora-e2e: own containers, network,
# volume and images) on its own host ports, so it runs while `make dev` is up
# and never touches that stack or its volume. Configuration is .env.example
# plus the overrides below; the developer's .env is never read, and the values
# from .env.example replace any exported in the shell. Exit code: Playwright's,
# or the failing step's.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT"

PROJECT=classora-e2e
E2E_POSTGRES_PORT=15432
E2E_PGBOUNCER_PORT=16432
E2E_API_PORT=18000
E2E_WEB_PORT=15173
RESULTS_DIR=e2e/test-results
PNPM=(env COREPACK_ENABLE_DOWNLOAD_PROMPT=0 corepack pnpm)

# Only .env.example (placeholders) plus explicit overrides: every variable it
# names is set from it, replacing any value exported in the developer's shell.
set -a
# shellcheck source=/dev/null
. ./.env.example
set +a
unset COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_PROFILES
export POSTGRES_PORT=$E2E_POSTGRES_PORT
export PGBOUNCER_PORT=$E2E_PGBOUNCER_PORT
export API_PORT=$E2E_API_PORT
export WEB_PORT=$E2E_WEB_PORT
# The host demo step reaches the E2E database on its published ports.
export DATABASE_URL="postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@127.0.0.1:$E2E_PGBOUNCER_PORT/$POSTGRES_DB"
export DATABASE_DIRECT_URL="postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@127.0.0.1:$E2E_POSTGRES_PORT/$POSTGRES_DB"

compose() {
  docker compose --project-name "$PROJECT" --project-directory "$ROOT" \
    --env-file .env.example -f compose.yaml -f compose.override.yaml "$@"
}

save_logs() {
  mkdir -p "$RESULTS_DIR"
  compose logs --no-color --timestamps >"$RESULTS_DIR/compose.log" 2>&1 || true
  echo "e2e: Compose logs saved to $RESULTS_DIR/compose.log"
}

teardown() {
  if [ "${E2E_KEEP:-}" = "1" ]; then
    echo "e2e: E2E_KEEP=1, the stack is still up:"
    echo "  web http://127.0.0.1:$E2E_WEB_PORT  api http://127.0.0.1:$E2E_API_PORT"
    echo "  remove it with: docker compose -p $PROJECT --env-file .env.example down -v"
    return
  fi
  echo "e2e: tearing down $PROJECT"
  compose down -v --remove-orphans >/dev/null 2>&1 || true
}

stack_started=0
on_exit() {
  local status=$?
  trap - EXIT INT TERM
  if [ "$stack_started" = 1 ]; then
    if [ "$status" -ne 0 ]; then
      save_logs
    fi
    teardown
  fi
  exit "$status"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

step() { echo "e2e: $*"; }

# Results of an earlier run never mix with this one.
rm -rf "$RESULTS_DIR" e2e/playwright-report

step "removing any leftover $PROJECT project"
stack_started=1
compose down -v --remove-orphans >/dev/null 2>&1 || true

step "building and starting the stack"
compose up -d --build --wait

step "applying migrations"
compose run --rm api alembic upgrade head

step "applying the reference seeds"
compose run --rm api python -m app.seeds

step "generating demo data (--seed 1)"
(cd apps/api && uv run python -m tests.factories.demo --seed 1)

step "installing headless Chromium (downloaded on first run only)"
"${PNPM[@]}" --filter @classora/e2e exec playwright install --only-shell chromium

step "running Playwright against http://127.0.0.1:$E2E_WEB_PORT"
E2E_BASE_URL="http://127.0.0.1:$E2E_WEB_PORT" "${PNPM[@]}" --filter @classora/e2e test
