#!/bin/sh
# Writes PgBouncer's auth file from the environment Compose passes in, then
# starts the given command (default: pgbouncer /etc/pgbouncer/pgbouncer.ini).
# The file exists only inside the running container (never in git or the
# image), and the password is never printed.
set -eu

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

# userlist.txt format: "user" "password"; a double quote inside a value is doubled.
quote() {
    printf '"%s"' "$(printf '%s' "$1" | sed 's/"/""/g')"
}

umask 077
auth_file=/run/pgbouncer/userlist.txt
printf '%s %s\n' "$(quote "$POSTGRES_USER")" "$(quote "$POSTGRES_PASSWORD")" > "$auth_file"

exec "$@"
