#!/usr/bin/env bash
# `make db-create NAME=<name>` / `make db-drop NAME=<name>`: a database and a
# role of the same name on the shared PostgreSQL server (`make db-shared`,
# infrastructure/docker/compose.shared-db.yaml).
#
#   shared-db.sh create <name>   idempotent: creates the role (LOGIN, local
#                                placeholder password) and the database it
#                                owns, if missing; also the cluster-wide
#                                runtime role classora_app once; prints the
#                                three URLs
#   shared-db.sh drop <name>     drops the database (closing its connections)
#                                and the role, if they exist; never
#                                classora_app (other databases use it)
#
# <name> must match [a-z0-9_]+ (at most 63 characters) and must not be a
# PostgreSQL system name. The role owns its database, so migrations can create
# the trusted extensions they need (pg_trgm, unaccent, btree_gist).
#
# The API and the worker log in as the least-privilege runtime role
# classora_app (#165, ADR-0003 amendment); migration 0096 grants it DML in
# each database. The worktree roles cannot CREATE ROLE, so this script (as
# postgres) creates it, with LOGIN and its local placeholder password.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
COMPOSE=(docker compose -f "$ROOT/infrastructure/docker/compose.shared-db.yaml")
PASSWORD=classora_local_only
RUNTIME_ROLE=classora_app
RUNTIME_PASSWORD=classora_app_local_only

usage() {
  echo "usage: make db-create NAME=<name> | make db-drop NAME=<name>  (name: [a-z0-9_]+)" >&2
  exit 2
}

[ $# -eq 2 ] || usage
action=$1
name=$2

if ! [[ $name =~ ^[a-z0-9_]+$ ]] || [ ${#name} -gt 63 ]; then
  echo "shared-db: invalid NAME '$name' (use [a-z0-9_]+, at most 63 characters)" >&2
  exit 2
fi
case $name in
  postgres | template0 | template1 | pg_* | "$RUNTIME_ROLE")
    echo "shared-db: NAME '$name' is reserved by PostgreSQL" >&2
    exit 2
    ;;
esac

if [ -z "$("${COMPOSE[@]}" ps --status running --quiet db 2>/dev/null)" ]; then
  echo "shared-db: the shared PostgreSQL server is not running; run 'make db-shared' first" >&2
  exit 1
fi

psql() {
  "${COMPOSE[@]}" exec -T db psql -U postgres -d postgres -v ON_ERROR_STOP=1 -qtA "$@"
}

port=$("${COMPOSE[@]}" port db 5432 | sed 's/.*://')

case $action in
  create)
    # The name is validated above, so it is safe in identifiers and literals.
    if [ -z "$(psql -c "SELECT 1 FROM pg_roles WHERE rolname = '$name'")" ]; then
      psql -c "CREATE ROLE \"$name\" LOGIN PASSWORD '$PASSWORD'"
      echo "shared-db: created role $name"
    fi
    if [ -z "$(psql -c "SELECT 1 FROM pg_roles WHERE rolname = '$RUNTIME_ROLE'")" ]; then
      psql -c "CREATE ROLE $RUNTIME_ROLE LOGIN NOINHERIT PASSWORD '$RUNTIME_PASSWORD'"
      echo "shared-db: created the runtime role $RUNTIME_ROLE"
    fi
    if [ -z "$(psql -c "SELECT 1 FROM pg_database WHERE datname = '$name'")" ]; then
      psql -c "CREATE DATABASE \"$name\" OWNER \"$name\""
      echo "shared-db: created database $name"
    else
      echo "shared-db: database $name already exists"
    fi
    owner_url="postgresql://$name:$PASSWORD@127.0.0.1:$port/$name"
    runtime_url="postgresql://$RUNTIME_ROLE:$RUNTIME_PASSWORD@127.0.0.1:$port/$name"
    echo "shared-db: set the three URLs (shell or this worktree's .env); no PgBouncer natively,"
    echo "shared-db: so both runtime URLs are direct. Run migrations before the API and worker."
    echo "  DATABASE_URL=$runtime_url"
    echo "  DATABASE_DIRECT_URL=$runtime_url"
    echo "  DATABASE_MIGRATION_URL=$owner_url"
    ;;
  drop)
    psql -c "DROP DATABASE IF EXISTS \"$name\" WITH (FORCE)"
    psql -c "DROP ROLE IF EXISTS \"$name\""
    echo "shared-db: dropped database and role $name (if they existed)"
    ;;
  *)
    usage
    ;;
esac
