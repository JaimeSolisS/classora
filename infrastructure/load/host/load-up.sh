#!/usr/bin/env bash
# Host side of dev-up.sh (#152), run as root on the DEV app host over SSM:
#   load-up.sh <s3 key of the dump> <lifted|default> <fresh|keep> [tuning]
# 1. unless classora_load exists and "keep": downloads the dump, (re)creates
#    classora_load owned by the database owner, pg_restore --no-owner
#    --exit-on-error (timed, for the RTO), then gives the runtime role
#    classora_app the grants migration 0096 gives in each database (the dump
#    carries no ACL; the role itself is cluster-wide and already has its
#    password, so sync-runtime-role has nothing to do);
# 2. writes the override (api and worker on classora_load; with "lifted" the
#    per-IP limit is 60000/min) and, with "tuning", stacks
#    /root/classora-load-tuning.yaml (already copied by dev-up.sh);
# 3. recreates api and worker with it and waits for readiness (180 s);
# 4. then counts every table of the real `classora` database once
#    (/root/classora-load-counts-before.txt; dev-down.sh compares). Read-only.
# It never writes to `classora`. No secret is printed: the override file uses
# Compose interpolation (${POSTGRES_APP_PASSWORD} from the env file).
set -euo pipefail

BIN=/opt/classora/bin
LOAD_DB=classora_load
OVERRIDE=/root/classora-load-override.yaml
TUNING=/root/classora-load-tuning.yaml
LOAD_COMPOSE=/root/classora-load-compose.sh
SCRATCH=/srv/classora-data/load-scratch
COUNTS=/root/classora-load-counts-before.txt

log() { echo "load-up: $(date -u +%H:%M:%S) $*"; }

[ $# -ge 3 ] || {
  echo "usage: load-up.sh <s3 key> <lifted|default> <fresh|keep> [tuning]" >&2
  exit 2
}
key=$1 limit=$2 mode=$3 tuning=${4:-}
# shellcheck source=/dev/null
. /etc/classora/host.env
[ "${CLASSORA_ENV:-}" = dev ] || {
  echo "load-up: this host is '${CLASSORA_ENV:-?}', not dev" >&2
  exit 3
}
[[ "$key" =~ ^backups/load-[0-9]{8}/classora\.dump$ ]] || {
  echo "load-up: unexpected key $key" >&2
  exit 2
}

psql_db() { # psql_db <database> <psql args...>, as the database owner in the db container
  local db=$1
  shift
  "$BIN/compose.sh" exec -T db sh -c "psql -X -q -v ON_ERROR_STOP=1 -U \"\$POSTGRES_USER\" -d $db $*"
}

# Exact row counts of every table in `classora` (read-only).
count_tables() {
  psql_db classora "-At -F ' '" <<'SQL'
SELECT format('SELECT %L, count(*) FROM %I.%I', tablename, schemaname, tablename)
FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename \gexec
SQL
}

exists=$(psql_db postgres "-At" <<<"SELECT 1 FROM pg_database WHERE datname = '$LOAD_DB'")
if [ "$exists" = 1 ] && [ "$mode" = keep ]; then
  log "$LOAD_DB exists; kept"
else
  mkdir -p "$SCRATCH"
  log "downloading s3://$OPS_BUCKET/$key"
  aws s3 cp --only-show-errors --region "$AWS_REGION" "s3://$OPS_BUCKET/$key" "$SCRATCH/classora.dump"
  log "dump size $(du -h "$SCRATCH/classora.dump" | cut -f1)"
  # Nothing may hold classora_load while it is replaced: api/worker go back
  # to classora first when they point at it.
  if [ "$exists" = 1 ] && [ -f "$OVERRIDE" ]; then
    "$BIN/compose.sh" up -d api worker >/dev/null 2>&1
  fi
  psql_db postgres "" <<SQL
DROP DATABASE IF EXISTS $LOAD_DB WITH (FORCE);
CREATE DATABASE $LOAD_DB;
SQL
  start=$(date +%s.%N)
  # shellcheck disable=SC2016 # expanded by the db container's shell
  "$BIN/compose.sh" exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d '"$LOAD_DB"' --no-owner --exit-on-error' \
    <"$SCRATCH/classora.dump"
  end=$(date +%s.%N)
  log "RESTORE_SECONDS=$(awk -v s="$start" -v e="$end" 'BEGIN { printf "%.1f", e - s }')"
  psql_db "$LOAD_DB" "" <<SQL
GRANT CONNECT ON DATABASE $LOAD_DB TO classora_app;
REVOKE TEMPORARY ON DATABASE $LOAD_DB FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO classora_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO classora_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO classora_app;
REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM classora_app;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM classora_app;
ANALYZE;
SQL
  rm -f "$SCRATCH/classora.dump"
  log "$LOAD_DB restored and granted; $(psql_db "$LOAD_DB" "-At" <<<"SELECT pg_size_pretty(pg_database_size('$LOAD_DB'))")"
fi

{
  echo "# Temporary load-test override (#152); removed by dev-down.sh."
  echo "services:"
  for service in api worker; do
    echo "  $service:"
    echo "    environment:"
    echo "      DATABASE_URL: postgresql://classora_app:\${POSTGRES_APP_PASSWORD}@pgbouncer:6432/$LOAD_DB"
    echo "      DATABASE_DIRECT_URL: postgresql://classora_app:\${POSTGRES_APP_PASSWORD}@db:5432/$LOAD_DB"
    if [ "$service" = api ] && [ "$limit" = lifted ]; then
      echo '      API_RATE_LIMIT_PER_MINUTE: "60000"'
    fi
  done
} >"$OVERRIDE"
chmod 0600 "$OVERRIDE"
[ "$tuning" = tuning ] || rm -f "$TUNING"

# compose.sh with every load override present (also used by the downtime probe).
cat >"$LOAD_COMPOSE" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
files=()
for f in /root/classora-load-override.yaml /root/classora-load-tuning.yaml; do
  [ -f "$f" ] && files+=(-f "$f")
done
exec /opt/classora/bin/compose.sh "${files[@]}" "$@"
SH
chmod 0700 "$LOAD_COMPOSE"

log "recreating api and worker on $LOAD_DB (rate limit: $limit, tuning: ${tuning:-none})"
"$LOAD_COMPOSE" up -d api worker
[ "$tuning" != tuning ] || "$LOAD_COMPOSE" up -d

deadline=$((SECONDS + 180))
until "$BIN/compose.sh" exec -T api python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)" >/dev/null 2>&1 &&
  "$BIN/compose.sh" exec -T worker python -m app.worker --healthcheck >/dev/null 2>&1; do
  [ "$SECONDS" -lt "$deadline" ] || {
    echo "load-up: api/worker not ready after 180 s" >&2
    exit 1
  }
  sleep 3
done
# Counted once api and worker no longer use classora (nothing writes to it
# from now on; dev-down.sh compares).
if [ ! -s "$COUNTS" ]; then
  count_tables >"$COUNTS"
  log "classora: $(wc -l <"$COUNTS") tables counted (before)"
fi

# The database each service uses (the name only) and the api's limit.
# shellcheck disable=SC2016 # expanded in the containers
for service in api worker; do
  log "$service database: $("$BIN/compose.sh" exec -T "$service" sh -c 'echo "${DATABASE_URL##*/} / ${DATABASE_DIRECT_URL##*/}"')"
done
# shellcheck disable=SC2016
log "api API_RATE_LIMIT_PER_MINUTE='$("$BIN/compose.sh" exec -T api sh -c 'echo "$API_RATE_LIMIT_PER_MINUTE"')'"
"$BIN/compose.sh" ps --format '{{.Service}} {{.Status}}'
log "load mode on"
