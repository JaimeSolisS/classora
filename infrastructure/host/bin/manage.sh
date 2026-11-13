#!/usr/bin/env bash
# Operator commands on the host, over Session Manager (#614; runbook
# _docs/runbooks/host.md). Run as root (sudo). Each runs in a one-off `migrate`
# container of the deployed API image, as the database owner on the direct
# connection (ADR-0003):
#   manage.sh bootstrap-owner <username> [--display-name <text>] [--password-stdin]
#                                              create the first Owner (prompts for
#                                              the password twice without --password-stdin)
#   manage.sh reset-owner-password <username>  print a one-time reset link
#   manage.sh migrate                          alembic upgrade head, then
#                                              sync-runtime-role and db-roles.sh
#   manage.sh reset-dev-data                   DEV only (#674): starts
#       classora-dev-data-reset.service (dev-data-reset.sh) and waits; prints
#       its JSON result line. Exit 2 on any other host, 3 without the marker.
# CLASSORA_TEST_ROOT (tests only) prefixes the host.env and marker paths.
set -euo pipefail

BIN=/opt/classora/bin
ROOT=${CLASSORA_TEST_ROOT:-}

usage() {
  echo "usage: manage.sh bootstrap-owner <username> [options] | reset-owner-password <username> | migrate | reset-dev-data" >&2
  exit 2
}

[ "$(id -u)" -eq 0 ] || {
  echo "manage.sh: run as root (sudo)" >&2
  exit 1
}
[ $# -ge 1 ] || usage

case "$1" in
  bootstrap-owner | reset-owner-password)
    [ $# -ge 2 ] || usage
    [[ "$2" =~ ^[A-Za-z0-9._-]+$ ]] || {
      echo "manage.sh: unexpected characters in the username" >&2
      exit 2
    }
    tty_flag=()
    [ -t 0 ] || tty_flag=(-T)
    exec "$BIN/compose.sh" --profile tools run --rm "${tty_flag[@]}" migrate \
      python -m app.cli "$1" --username "$2" "${@:3}"
    ;;
  migrate)
    [ $# -eq 1 ] || usage
    "$BIN/compose.sh" --profile tools run --rm -T migrate \
      sh -c 'alembic upgrade head && python -m app.cli sync-runtime-role'
    "$BIN/db-roles.sh"
    ;;
  reset-dev-data)
    [ $# -eq 1 ] || usage
    env_name=$(sed -n 's/^CLASSORA_ENV=//p' "$ROOT/etc/classora/host.env" 2>/dev/null | tail -n 1 || true)
    [ "$env_name" = dev ] || {
      echo "manage.sh: reset-dev-data runs on DEV only; this host is '${env_name:-unknown}'" >&2
      exit 2
    }
    [ -f "$ROOT/srv/classora-data/dev-data/ENABLED" ] || {
      echo "manage.sh: /srv/classora-data/dev-data/ENABLED is missing: take the pre-wipe backup first (runbook dev-data.md)" >&2
      exit 3
    }
    since=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
    rc=0
    systemctl start --wait classora-dev-data-reset.service || rc=$?
    journalctl -u classora-dev-data-reset.service --since "$since" -o cat --no-pager |
      grep '^{"event": "dev-data-reset"' | tail -n 1 || echo "manage.sh: no result line; see journalctl -u classora-dev-data-reset" >&2
    exit "$rc"
    ;;
  *) usage ;;
esac
