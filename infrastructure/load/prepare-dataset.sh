#!/usr/bin/env bash
# Build the scrubbed load-test dataset on THIS machine (#152, M10-04; runbook
# _docs/runbooks/load-test.md, ADR-0018 "data exception"):
#   infrastructure/load/prepare-dataset.sh [--upload]
# 1. (re)creates its own database on the shared native PostgreSQL server
#    (`make db-shared`, 127.0.0.1:55432): LOAD_PREP_DB, default
#    classora_load_prep. Never the owner's `classora` (refused);
# 2. migrates it, then runs the simulation (FULL profile, Spanish) into it:
#    the same as `make seed-data-spanish`, with this database's URLs;
# 3. scrubs it (scrub.py): new random passwords for every sign-in account,
#    written only to infrastructure/load/accounts.local.json (gitignored,
#    0600); sessions, account tokens, idempotency keys and rate-limit counters
#    emptied; the current privacy notice accepted; the README's
#    classora-sim-* passwords proven to fail;
# 4. pg_dump -Fc --no-owner --no-acl to infrastructure/load/out/classora.dump
#    (gitignored) and prints the main tables' row counts;
# 5. --upload: copies the dump to the DEV ops bucket (profile jsolisdev) under
#    backups/load-<YYYYMMDD>/classora.dump, the only prefix the DEV host may
#    read. restore.sh "latest" only matches backups/<YYYY-MM-DD>/, so it never
#    picks this object; the bucket's 14-day lifecycle removes it if
#    dev-down.sh does not.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
LOAD=$ROOT/infrastructure/load
DB=${LOAD_PREP_DB:-classora_load_prep}
ACCOUNTS=$LOAD/accounts.local.json
OUT=$LOAD/out
SHARED=(docker compose -f "$ROOT/infrastructure/docker/compose.shared-db.yaml")

upload=false
case "${1:-}" in
  "") ;;
  --upload) upload=true ;;
  *)
    echo "usage: prepare-dataset.sh [--upload]" >&2
    exit 2
    ;;
esac

log() { echo "prepare-dataset: $(date -u +%H:%M:%S) $*" >&2; }

case $DB in
  classora | postgres | template0 | template1)
    echo "prepare-dataset: refusing database '$DB'" >&2
    exit 2
    ;;
esac

log "recreating database $DB on the shared server (make db-shared)"
"$ROOT/infrastructure/scripts/shared-db.sh" drop "$DB" >/dev/null
"$ROOT/infrastructure/scripts/shared-db.sh" create "$DB" >/dev/null
port=$("${SHARED[@]}" port db 5432 | sed 's/.*://')
[ "$port" != 5432 ] && [ "$port" != 6432 ] || {
  echo "prepare-dataset: the shared server is on port $port, refusing" >&2
  exit 2
}

# The shared server's local placeholder credentials (shared-db.sh), not secrets.
export APP_ENV=local
export APP_ORIGIN=http://127.0.0.1:5173
export SESSION_COOKIE_SECURE=false
export DATABASE_URL="postgresql://classora_app:classora_app_local_only@127.0.0.1:$port/$DB"
export DATABASE_DIRECT_URL=$DATABASE_URL
export DATABASE_MIGRATION_URL="postgresql://$DB:classora_local_only@127.0.0.1:$port/$DB"
log "target: host 127.0.0.1, port $port, database $DB"

cd "$ROOT/apps/api"
log "migrating"
uv run alembic upgrade head
log "generating the simulation (FULL, es); a few minutes"
uv run python -m tests.factories.simulation --language es
log "scrubbing"
PYTHONPATH=. uv run python "$LOAD/scrub.py" "$DATABASE_MIGRATION_URL" "$ACCOUNTS"

mkdir -p "$OUT"
log "dumping to $OUT/classora.dump"
"${SHARED[@]}" exec -T db pg_dump -U postgres -Fc --no-owner --no-acl -d "$DB" >"$OUT/classora.dump"
log "dump size: $(du -h "$OUT/classora.dump" | cut -f1)"
"${SHARED[@]}" exec -T db psql -U postgres -d "$DB" -qAt -F ' ' -c \
  "SELECT relname, n_live_tup FROM pg_stat_user_tables WHERE n_live_tup > 0 ORDER BY n_live_tup DESC LIMIT 15" |
  sed 's/^/prepare-dataset: rows /' >&2

if [ "$upload" = true ]; then
  export AWS_PROFILE=${AWS_PROFILE:-jsolisdev} AWS_REGION=${AWS_REGION:-us-east-2} AWS_PAGER=""
  [ "$AWS_PROFILE" = jsolisdev ] || {
    echo "prepare-dataset: AWS_PROFILE must be jsolisdev" >&2
    exit 2
  }
  # shellcheck disable=SC2016 # a JMESPath literal
  bucket=$(aws s3api list-buckets --query 'Buckets[?starts_with(Name, `classora-dev-ops-`)].Name' --output text)
  [ -n "$bucket" ] && [ "$(wc -w <<<"$bucket")" -eq 1 ] || {
    echo "prepare-dataset: expected exactly one classora-dev-ops-* bucket, got '$bucket'" >&2
    exit 1
  }
  key="backups/load-$(date -u +%Y%m%d)/classora.dump"
  log "uploading to s3://$bucket/$key"
  aws s3 cp --only-show-errors "$OUT/classora.dump" "s3://$bucket/$key"
fi
log "done"
