#!/usr/bin/env bash
# Runs a command with the variables of an env file (default: .env at the root
# of this checkout or worktree; ENV_FILE=<path> picks another), for the native
# local stack (`make api-native`, `make worker-native`, `make web-native`).
#
#   with-env.sh <command> [args...]
#
# The file uses the same KEY=VALUE format and keys as Compose's .env (see
# .env.example). A variable already set in the environment wins over the file,
# so one worktree can run a second stack with overrides, for example
#   API_PORT=8102 WEB_PORT=5275 APP_ORIGIN=http://127.0.0.1:5275 make api-native
# Blank lines and # comments are skipped; one pair of surrounding quotes is
# removed from a value. No other expansion is done.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
env_file=${ENV_FILE:-$ROOT/.env}

if [ ! -f "$env_file" ]; then
  echo "with-env: $env_file not found; copy .env.example to .env (or set ENV_FILE)" >&2
  exit 1
fi

while IFS= read -r line || [ -n "$line" ]; do
  line=${line%$'\r'}
  case $line in
    '' | '#'*) continue ;;
  esac
  line=${line#export }
  key=${line%%=*}
  [ "$key" != "$line" ] || continue
  [[ $key =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
  # Already set in the environment (even to empty): keep it.
  [ -z "${!key+x}" ] || continue
  value=${line#*=}
  if [[ $value =~ ^\"(.*)\"$ || $value =~ ^\'(.*)\'$ ]]; then
    value=${BASH_REMATCH[1]}
  fi
  export "$key=$value"
done <"$env_file"

exec "$@"
