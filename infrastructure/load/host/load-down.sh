#!/usr/bin/env bash
# Host side of dev-down.sh (#152), run as root on the DEV app host over SSM.
# Idempotent:
# 1. recreates every service from the release's own Compose files (no load
#    override, so api and worker are back on `classora` with the default
#    per-IP limit, and any tuning override is gone) and waits for readiness;
# 2. checks, from the containers' environment, that api and worker use
#    `classora` and that the api has no API_RATE_LIMIT_PER_MINUTE override;
# 3. DROP DATABASE classora_load; removes the override files, the scratch
#    directory and the helper;
# 4. compares the exact row counts of `classora` with the ones load-up.sh
#    took before load mode (must be identical: nothing pointed at it).
set -euo pipefail

BIN=/opt/classora/bin
LOAD_DB=classora_load
COUNTS=/root/classora-load-counts-before.txt

log() { echo "load-down: $(date -u +%H:%M:%S) $*"; }

# shellcheck source=/dev/null
. /etc/classora/host.env
[ "${CLASSORA_ENV:-}" = dev ] || {
  echo "load-down: this host is '${CLASSORA_ENV:-?}', not dev" >&2
  exit 3
}

psql_db() {
  local db=$1
  shift
  "$BIN/compose.sh" exec -T db sh -c "psql -X -q -v ON_ERROR_STOP=1 -U \"\$POSTGRES_USER\" -d $db $*"
}

ok=1
# Counted while api and worker still point away from classora (a worker on
# classora would start its periodic jobs and write).
if [ -s "$COUNTS" ]; then
  after=$(psql_db classora "-At -F ' '" <<'SQL'
SELECT format('SELECT %L, count(*) FROM %I.%I', tablename, schemaname, tablename)
FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename \gexec
SQL
)
  if diff <(cat "$COUNTS") <(echo "$after") >/dev/null; then
    log "classora row counts unchanged ($(wc -l <"$COUNTS") tables)"
    rm -f "$COUNTS"
  else
    log "classora row counts CHANGED:"
    diff <(cat "$COUNTS") <(echo "$after") || true
    ok=0
  fi
else
  log "no before-counts file (load mode was not on)"
fi
rm -f /root/classora-load-override.yaml /root/classora-load-tuning.yaml /root/classora-load-compose.sh
log "recreating the release's services without the load override"
"$BIN/compose.sh" up -d --wait --wait-timeout 180

# shellcheck disable=SC2016 # expanded in the containers
for service in api worker; do
  dbs=$("$BIN/compose.sh" exec -T "$service" sh -c 'echo "${DATABASE_URL##*/} ${DATABASE_DIRECT_URL##*/}"')
  log "$service database: $dbs"
  [ "$dbs" = "classora classora" ] || ok=0
done
# shellcheck disable=SC2016
limit=$("$BIN/compose.sh" exec -T api sh -c 'echo "$API_RATE_LIMIT_PER_MINUTE"')
log "api API_RATE_LIMIT_PER_MINUTE='$limit' (empty: the default 600)"
[ -z "$limit" ] || ok=0

psql_db postgres "" <<<"DROP DATABASE IF EXISTS $LOAD_DB WITH (FORCE);"
left=$(psql_db postgres "-At" <<<"SELECT count(*) FROM pg_database WHERE datname = '$LOAD_DB'")
log "$LOAD_DB databases left: $left"
[ "$left" = 0 ] || ok=0
rm -rf /srv/classora-data/load-scratch

"$BIN/compose.sh" ps --format '{{.Service}} {{.Status}}'
if [ "$ok" != 1 ]; then
  echo "load-down: a check failed (see above)" >&2
  exit 1
fi
log "DEV back to normal"
