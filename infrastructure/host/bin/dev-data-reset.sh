#!/usr/bin/env bash
# DEV data reset (#674, ADR-0018 amendment "DEV data"; runbook
# _docs/runbooks/dev-data.md), as root, DEV only:
#   dev-data-reset.sh            wipe `classora` and restore the English
#                                simulated center with the `visitor` Owner
#   dev-data-reset.sh --check    print the plan; changes nothing
# Run nightly at 04:00 UTC by classora-dev-data-reset.timer (the unit only
# starts once /srv/classora-data/dev-data/ENABLED exists, created after the
# verified pre-wipe backup), or by hand with
# `manage.sh reset-dev-data`, which starts the same unit.
# Steps:
#   1. refuses (exit 2) unless /etc/classora/host.env says CLASSORA_ENV=dev
#      and, for a real run, unless the ENABLED marker exists;
#   2. waits up to 15 minutes for the backup lock, then the deploy lock (the
#      same order as backup.sh), so it never overlaps a backup or a deploy;
#   3. the dump: the current release's releases/<sha>.dev-data.dump and its
#      counts (built by deploy-dev's dev-data job), cached in
#      /srv/classora-data/dev-data/, else downloaded; if neither works, the
#      newest cached dump (step 6's migrations bring it to the current head);
#      none: fails before anything is stopped;
#   4. stops api, worker and pgbouncer (no pooled connection survives into
#      the new database);
#   5. drops and re-creates `classora` and restores the dump (pg_restore
#      --no-owner --exit-on-error), then counts every table and compares with
#      the dump's counts (before anything else writes);
#   6. re-applies the runtime-role grants migration 0096 gives (the dump has
#      no ACL, as load-up.sh), then the deploy's database step in a one-off
#      container of the current API image: alembic upgrade head,
#      sync-runtime-role, the reference seeds; then db-roles.sh;
#   7. starts every service and runs deploy.sh's 90 s readiness gate (api,
#      worker, the edge's / through Caddy);
#   8. keeps the 3 newest cached dumps.
# The last stdout line (the journal) is one JSON object, also appended to
# /var/log/classora/dev-data-reset.log (Loki: {service_name="dev-data-reset"}):
#   {"event": "dev-data-reset", "env": "dev", "status": "ok|mismatch|failed",
#    "release": "<sha>", "dump": "<sha of the dump>", "fallback": true|false,
#    "tables": N, "mismatched": N, "duration_s": N[, "error": "..."]}
# Exit 0 ok; 1 mismatch or failed (api and worker are started again whatever
# happened); 2 refused or usage. No secret is printed and xtrace is never on.
#
# CLASSORA_TEST_ROOT (tests only, apps/api/tests/unit/test_dev_data_reset.py)
# prefixes every path, the scripts in $BIN included, so a test runs it
# against a fake tree and fake commands; root is then not required.
set -euo pipefail

ROOT=${CLASSORA_TEST_ROOT:-}
HOST_ENV=$ROOT/etc/classora/host.env
BIN=$ROOT/opt/classora/bin
DATA=$ROOT/srv/classora-data
RELEASES=$DATA/releases
CACHE=$DATA/dev-data
MARKER=$CACHE/ENABLED
LOG_DIR=$ROOT/var/log/classora
LOG=$LOG_DIR/dev-data-reset.log
LOCK_DIR=$ROOT/run
HEALTH_SECONDS=90
LOCK_SECONDS=900
KEEP_DUMPS=3

usage() {
  echo "usage: dev-data-reset.sh [--check]" >&2
  exit 2
}

check=false
case "$#:${1:-}" in
  0:) ;;
  1:--check) check=true ;;
  *) usage ;;
esac

# 1. DEV only, before anything else (a missing file or value refuses too).
env_name=$(sed -n 's/^CLASSORA_ENV=//p' "$HOST_ENV" 2>/dev/null | tail -n 1 || true)
if [ "$env_name" != dev ]; then
  echo "dev-data-reset: refused: this host is '${env_name:-unknown}', not dev (CLASSORA_ENV in $HOST_ENV)" >&2
  exit 2
fi
if [ -z "$ROOT" ] && [ "$(id -u)" -ne 0 ]; then
  echo "dev-data-reset: run as root (sudo)" >&2
  exit 2
fi
# shellcheck source=/dev/null
. "$HOST_ENV"
: "${OPS_BUCKET:?}" "${AWS_REGION:?}"
# shellcheck source=/dev/null # (backup-common.sh: COUNTS_SQL)
. "$BIN/backup-common.sh"

release=""
dump_release=""
fallback=false
tables=0
mismatched=0
reported=0
stopped=0
tmp=""

log() { echo "dev-data-reset: $(date -u +%H:%M:%S) $*" >&2; }

emit() { # emit <status> [error]
  reported=1
  local error="" line
  [ -z "${2:-}" ] || error=$(printf ', "error": "%s"' "${2//\"/\'}")
  line=$(printf '{"event": "dev-data-reset", "env": "%s", "status": "%s", "release": "%s", "dump": "%s", "fallback": %s, "tables": %d, "mismatched": %d, "duration_s": %d%s}' \
    "$env_name" "$1" "$release" "$dump_release" "$fallback" "$tables" "$mismatched" "$SECONDS" "$error")
  echo "$line"
  install -d -m 0755 "$LOG_DIR"
  printf '%s\n' "$line" >>"$LOG" || true
}

fail() {
  log "$1"
  emit failed "$1"
  exit 1
}

# shellcheck disable=SC2329 # invoked by the EXIT trap
on_exit() {
  local code=$?
  [ -z "$tmp" ] || rm -rf "$tmp"
  if [ "$stopped" -eq 1 ]; then
    # Never leave the site without its api and worker.
    "$BIN/compose.sh" up -d >/dev/null 2>&1 || log "WARNING: could not start the services again"
  fi
  if [ "$reported" -eq 0 ]; then
    log "failed (exit $code)"
    emit failed "unexpected error (exit $code)"
    code=1
  fi
  exit "$code"
}

current_release() {
  local dir
  dir=$(readlink -f "$RELEASES/current" 2>/dev/null || true)
  dir=$(basename "${dir:-none}")
  [[ "$dir" =~ ^[0-9a-f]{40}$ ]] && echo "$dir"
}

# The newest cached dump that has its counts, as "<sha>".
newest_cached() {
  local f sha list
  # shellcheck disable=SC2012 # names are <40-hex>.dev-data.dump (no spaces)
  list=$(ls -1t "$CACHE"/*.dev-data.dump 2>/dev/null || true)
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    sha=$(basename "$f" .dev-data.dump)
    if [[ "$sha" =~ ^[0-9a-f]{40}$ ]] && [ -s "$CACHE/$sha.dev-data.counts.json" ]; then
      echo "$sha"
      return 0
    fi
  done <<<"$list"
  return 1
}

s3_key() { echo "releases/$1.dev-data.$2"; }

release=$(current_release || true)

if [ "$check" = true ]; then
  echo "dev-data-reset --check: nothing is changed"
  echo "env:      $env_name"
  if [ -f "$MARKER" ]; then
    echo "marker:   $MARKER present (the timer and manage.sh reset-dev-data run)"
  else
    echo "marker:   $MARKER MISSING (the unit is skipped; create it after the pre-wipe backup, runbook dev-data.md)"
  fi
  echo "release:  ${release:-NONE (no current release)}"
  source_line=""
  if [ -n "$release" ] && [ -s "$CACHE/$release.dev-data.dump" ] && [ -s "$CACHE/$release.dev-data.counts.json" ]; then
    source_line="cached $CACHE/$release.dev-data.dump"
  elif [ -n "$release" ] && aws s3api head-object --region "$AWS_REGION" --bucket "$OPS_BUCKET" \
    --key "$(s3_key "$release" dump)" >/dev/null 2>&1; then
    source_line="download s3://$OPS_BUCKET/$(s3_key "$release" dump)"
  elif cached=$(newest_cached); then
    source_line="fallback: the newest cached dump $CACHE/$cached.dev-data.dump, then migrations"
  fi
  echo "dump:     ${source_line:-NONE (a reset would fail before stopping anything)}"
  echo "plan:     wait for the backup and deploy locks (up to ${LOCK_SECONDS} s each); stop api, worker, pgbouncer;"
  echo "          drop and re-create the database; pg_restore; compare row counts; 0096 grants;"
  echo "          migrate, sync-runtime-role, seeds, db-roles.sh; start; ${HEALTH_SECONDS} s readiness gate"
  echo "log:      $LOG and the journal (classora-dev-data-reset.service)"
  [ -n "$release" ] && [ -n "$source_line" ] || exit 1
  exit 0
fi

# The marker is the operator's go-ahead (runbook dev-data.md, after the
# verified pre-wipe backup); the unit has the same condition.
[ -f "$MARKER" ] || {
  echo "dev-data-reset: refused: $MARKER is missing (create it after the pre-wipe backup)" >&2
  exit 2
}
trap on_exit EXIT

# 2. Locks: backup first, then deploy (backup.sh's order, so no deadlock).
exec 8>"$LOCK_DIR/classora-backup.lock"
flock -w "$LOCK_SECONDS" 8 || fail "the backup lock was held for $LOCK_SECONDS s"
exec 9>"$LOCK_DIR/classora-deploy.lock"
flock -w "$LOCK_SECONDS" 9 || fail "the deploy lock was held for $LOCK_SECONDS s"
log "locks taken"
[ -n "$ROOT" ] || mountpoint -q "$DATA" || fail "$DATA is not mounted"
# Read again under the deploy lock: a deploy that just finished moved it.
release=$(current_release) || fail "no current release in $RELEASES"

# 3. The dump and its counts.
install -d -m 0700 "$CACHE"
if [ -s "$CACHE/$release.dev-data.dump" ] && [ -s "$CACHE/$release.dev-data.counts.json" ]; then
  dump_release=$release
  log "using the cached dump of $release"
else
  tmp=$(mktemp -d "$CACHE/.download.XXXXXX")
  if aws s3 cp --only-show-errors --region "$AWS_REGION" \
    "s3://$OPS_BUCKET/$(s3_key "$release" counts.json)" "$tmp/counts.json" &&
    aws s3 cp --only-show-errors --region "$AWS_REGION" \
      "s3://$OPS_BUCKET/$(s3_key "$release" dump)" "$tmp/dump"; then
    mv -f "$tmp/counts.json" "$CACHE/$release.dev-data.counts.json"
    mv -f "$tmp/dump" "$CACHE/$release.dev-data.dump"
    dump_release=$release
    log "downloaded the dump of $release ($(du -h "$CACHE/$release.dev-data.dump" | cut -f1))"
  elif dump_release=$(newest_cached); then
    fallback=true
    log "WARNING: no dump for $release; using the cached dump of $dump_release (migrations follow)"
  else
    fail "no dump for $release and none cached"
  fi
  rm -rf "$tmp"
  tmp=""
fi
dump=$CACHE/$dump_release.dev-data.dump
counts_file=$CACHE/$dump_release.dev-data.counts.json
python3 -c 'import json,sys; t=json.load(open(sys.argv[1]))["tables"]; assert isinstance(t, dict) and t' \
  "$counts_file" 2>/dev/null || fail "unreadable counts file $counts_file"
touch "$dump" "$counts_file" # the newest-used order of the cache

# psql_db <database> [psql args]: as the database owner, in the db container.
psql_db() {
  local db=$1
  shift
  # shellcheck disable=SC2016 # expanded by the db container's shell
  "$BIN/compose.sh" exec -T db sh -c 'db=$1; shift; exec psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$db" "$@"' sh "$db" "$@"
}

[ -n "$("$BIN/compose.sh" ps --status running --quiet db 2>/dev/null)" ] || fail "the db container is not running"
# shellcheck disable=SC2016 # expanded by the db container's shell
database=$("$BIN/compose.sh" exec -T db sh -c 'echo "$POSTGRES_DB"' | tr -d '\r')
[[ "$database" =~ ^[a-z_][a-z0-9_]*$ ]] || fail "unexpected database name"

# 4. Stop the database's users (pgbouncer too: no stale pooled connection).
log "stopping api, worker and pgbouncer"
stopped=1
"$BIN/compose.sh" stop api worker pgbouncer >/dev/null || fail "could not stop api, worker and pgbouncer"

# 5. Re-create and restore.
log "re-creating $database and restoring the dump of $dump_release"
psql_db postgres <<SQL || fail "could not re-create $database"
DROP DATABASE IF EXISTS $database WITH (FORCE);
CREATE DATABASE $database;
SQL
# shellcheck disable=SC2016 # expanded by the db container's shell
"$BIN/compose.sh" exec -T db sh -c 'exec pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error' \
  <"$dump" || fail "pg_restore failed"

restored=$(printf '%s\n' "$COUNTS_SQL" | psql_db "$database" -At) || fail "could not count the restored tables"
set +e
summary=$(RESTORED=$restored COUNTS_FILE=$counts_file python3 - <<'PY'
import json, os, sys
restored = json.loads(os.environ["RESTORED"])
source = json.load(open(os.environ["COUNTS_FILE"]))["tables"]
names = sorted(set(source) | set(restored))
bad = [n for n in names if source.get(n) != restored.get(n)]
for n in bad:
    print(f"dev-data-reset: MISMATCH {n}: dump {source.get(n)}, restored {restored.get(n)}", file=sys.stderr)
print(len(names), len(bad))
PY
)
rc=$?
set -e
[ "$rc" -eq 0 ] || fail "could not compare the row counts"
read -r tables mismatched <<<"$summary"
log "$tables tables counted, $mismatched mismatched"

# 6. Grants (migration 0096; the dump carries no ACL), then the deploy's
# database step and the monitor role.
psql_db "$database" <<SQL || fail "could not grant the runtime role"
GRANT CONNECT ON DATABASE $database TO classora_app;
REVOKE TEMPORARY ON DATABASE $database FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO classora_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO classora_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO classora_app;
REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM classora_app;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM classora_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO classora_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO classora_app;
ANALYZE;
SQL
log "migrating (alembic upgrade head, sync-runtime-role, seeds)"
"$BIN/compose.sh" --profile tools run --rm -T migrate \
  sh -c 'alembic upgrade head && python -m app.cli sync-runtime-role && python -m app.seeds' >&2 ||
  fail "the database step failed"
"$BIN/db-roles.sh" >&2 || fail "db-roles.sh failed"

# 7. Start and the readiness gate (deploy.sh's checks).
api_ready() {
  "$BIN/compose.sh" exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)" \
    >/dev/null 2>&1
}
worker_ready() {
  "$BIN/compose.sh" exec -T worker python -m app.worker --healthcheck >/dev/null 2>&1
}
edge_ready() {
  local headers
  headers=$(curl -fsk -o /dev/null -D - --max-time 5 --resolve "$1:443:127.0.0.1" "https://$1/" 2>/dev/null) &&
    grep -qi '^content-security-policy:' <<<"$headers"
}
log "starting the services"
"$BIN/compose.sh" up -d --wait --wait-timeout 180 >&2 || fail "the services did not start"
stopped=0
host=$(sed -n 's/^SITE_ADDRESS=//p' "$ROOT/opt/classora/env/app.env" | cut -d, -f1 | tr -d ' ')
deadline=$((SECONDS + HEALTH_SECONDS))
failing=" not checked"
while [ "$SECONDS" -lt "$deadline" ]; do
  failing=""
  api_ready || failing="$failing api"
  worker_ready || failing="$failing worker"
  edge_ready "$host" || failing="$failing edge"
  [ -n "$failing" ] || break
  sleep 3
done
[ -z "$failing" ] || fail "not ready after ${HEALTH_SECONDS} s:$failing"

# 8. Keep the newest cached dumps (the one just used is the newest).
# shellcheck disable=SC2012 # names are <40-hex>.dev-data.dump
ls -1t "$CACHE"/*.dev-data.dump 2>/dev/null | tail -n +$((KEEP_DUMPS + 1)) |
  while IFS= read -r old; do
    rm -f "$old" "${old%.dump}.counts.json"
  done

if [ "$mismatched" -eq 0 ]; then
  log "done: $database holds the dump of $dump_release"
  emit ok
  exit 0
fi
emit mismatch "$mismatched of $tables tables differ from the dump's counts"
exit 1
