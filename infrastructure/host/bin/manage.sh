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
set -euo pipefail

BIN=/opt/classora/bin

usage() {
  echo "usage: manage.sh bootstrap-owner <username> [options] | reset-owner-password <username> | migrate" >&2
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
  *) usage ;;
esac
