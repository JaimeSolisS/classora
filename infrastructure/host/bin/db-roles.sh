#!/usr/bin/env bash
# Database roles the deploy manages outside Alembic (#614), as root, after
# the migrations. Idempotent.
#   classora_monitor  LOGIN, pg_monitor, SELECT on procrastinate_jobs and
#                     procrastinate_events (collector agent, ADR-0014); its
#                     password is /classora/<env>/monitor_db_password.
# The runtime role classora_app is created by migration 0096 and gets its
# password from `python -m app.cli sync-runtime-role` (deploy.sh, #165).
# The password reaches psql through the environment (psql \getenv), never argv
# or output; statement logging is off for the session.
set -euo pipefail

BIN=/opt/classora/bin
env_file=/opt/classora/env/app.env

MONITOR_PASSWORD=$(sed -n 's/^PG_MONITOR_PASSWORD=//p' "$env_file")
[ -n "$MONITOR_PASSWORD" ] || {
  echo "db-roles: PG_MONITOR_PASSWORD missing from $env_file" >&2
  exit 1
}
export MONITOR_PASSWORD

# shellcheck disable=SC2016 # expanded by the db container's shell
"$BIN/compose.sh" exec -T -e MONITOR_PASSWORD db \
  sh -c 'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
\getenv pw MONITOR_PASSWORD
\getenv db POSTGRES_DB
SET log_statement = 'none';
SELECT 'CREATE ROLE classora_monitor'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'classora_monitor') \gexec
ALTER ROLE classora_monitor WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT PASSWORD :'pw';
GRANT pg_monitor TO classora_monitor;
GRANT CONNECT ON DATABASE :"db" TO classora_monitor;
GRANT USAGE ON SCHEMA public TO classora_monitor;
GRANT SELECT ON procrastinate_jobs, procrastinate_events TO classora_monitor;
SQL
echo "db-roles: classora_monitor is up to date"
