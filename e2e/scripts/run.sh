#!/usr/bin/env bash
# `make e2e`: run the Playwright tests against an isolated, freshly seeded
# Compose stack, then tear it down (rules: _docs/testing-guidelines.md, E2E).
#
#   1. remove any leftover classora-e2e project (down -v)
#   2. start db only (up -d --wait db)
#   3. alembic upgrade head + python -m app.cli sync-runtime-role (migrate
#      tools service, owner, direct connection; ADR-0003 amendment, #165)
#   4. python -m app.seeds       (migrate service, owner)
#   5. build and start every service (up -d --build --wait): api, worker and
#      PgBouncer log in as the runtime role classora_app, which step 3 created
#   6. check: every app connection in pg_stat_activity is classora_app
#   7. demo command, --seed 1 --without-billing (host, uv, owner URL)
#   8. E2E attendance fixture    (host, uv, owner URL; a started session, #76)
#   9. headless Chromium         (downloaded on first run only)
#  10. Playwright
#  11. on failure: Compose logs -> e2e/test-results/compose.log
#  12. always: down -v (also after a failure or Ctrl-C), unless E2E_KEEP=1
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
# The API accepts unsafe requests only from the web origin of this stack.
export APP_ORIGIN=http://127.0.0.1:$E2E_WEB_PORT
# The host demo steps reach the E2E database on its published ports: they
# write as the owner (DATABASE_MIGRATION_URL); the runtime URLs only validate.
export DATABASE_URL="postgresql://classora_app:$POSTGRES_APP_PASSWORD@127.0.0.1:$E2E_PGBOUNCER_PORT/$POSTGRES_DB"
export DATABASE_DIRECT_URL="postgresql://classora_app:$POSTGRES_APP_PASSWORD@127.0.0.1:$E2E_POSTGRES_PORT/$POSTGRES_DB"
export DATABASE_MIGRATION_URL="postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@127.0.0.1:$E2E_POSTGRES_PORT/$POSTGRES_DB"

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

step "starting the database"
compose up -d --wait db

step "applying migrations and the runtime role's login"
compose run --rm --build migrate sh -c 'alembic upgrade head && python -m app.cli sync-runtime-role'

step "applying the reference seeds"
compose run --rm migrate python -m app.seeds

step "building and starting the stack"
compose up -d --build --wait

step "checking that the api, worker and PgBouncer connect as classora_app"
app_roles=$(compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT string_agg(DISTINCT usename, ',') FROM pg_stat_activity
   WHERE datname = current_database() AND backend_type = 'client backend'
     AND pid <> pg_backend_pid()")
echo "e2e: app connections as: $app_roles"
if [ "$app_roles" != "classora_app" ]; then
  echo "e2e: expected only classora_app connections, got '$app_roles'" >&2
  exit 1
fi

step "generating demo data (--seed 1)"
# --without-billing: billing stays off in the E2E database (#366, M6-demo E3).
(cd apps/api && uv run python -m tests.factories.demo --seed 1 --without-billing)

step "inserting the E2E attendance session"
(cd apps/api && uv run python -m tests.factories.e2e_attendance)

step "installing headless Chromium (downloaded on first run only)"
"${PNPM[@]}" --filter @classora/e2e exec playwright install --only-shell chromium

step "running Playwright against http://127.0.0.1:$E2E_WEB_PORT"
E2E_BASE_URL="http://127.0.0.1:$E2E_WEB_PORT" "${PNPM[@]}" --filter @classora/e2e test
