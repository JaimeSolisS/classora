#!/bin/sh
# Writes PgBouncer's auth file from the environment Compose passes in, then
# starts the given command (default: pgbouncer /etc/pgbouncer/pgbouncer.ini).
# The file exists only inside the running container (never in git or the
# image), and the password is never printed. It holds ONE user: the
# least-privilege runtime role the API logs in as (classora_app, #165); the
# database owner never goes through PgBouncer.
set -eu

: "${PGBOUNCER_AUTH_USER:?PGBOUNCER_AUTH_USER is required}"
: "${PGBOUNCER_AUTH_PASSWORD:?PGBOUNCER_AUTH_PASSWORD is required}"

# userlist.txt format: "user" "password"; a double quote inside a value is doubled.
quote() {
    printf '"%s"' "$(printf '%s' "$1" | sed 's/"/""/g')"
}

umask 077
auth_file=/run/pgbouncer/userlist.txt
printf '%s %s\n' "$(quote "$PGBOUNCER_AUTH_USER")" "$(quote "$PGBOUNCER_AUTH_PASSWORD")" > "$auth_file"

exec "$@"
