#!/usr/bin/env bash
# Decide which CI job groups a pull request needs (used by the `changes` job in
# .github/workflows/ci.yml). Compares <head> with its merge base on <base>
# (`git diff base...head`) and prints `api=true|false` and `web=true|false`.
#
# Run it locally against any two refs:
#   bash .github/scripts/changes.sh <base-ref> <head-ref>
#
# Groups:
#   api: apps/api/**, infrastructure/docker/pgbouncer/** (built by the integration tests)
#   web: apps/web/**, packages/**, package.json, pnpm-lock.yaml,
#        pnpm-workspace.yaml, .npmrc, .nvmrc
#   all (api and web): .github/workflows/**, .github/scripts/**, Makefile
# Anything else (docs, README, Compose, other infrastructure, e2e) runs neither.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <base-ref> <head-ref>" >&2
  exit 2
fi

files=$(git diff --name-only "$1...$2")

api=false
web=false
while IFS= read -r file; do
  case "$file" in
    .github/workflows/* | .github/scripts/* | Makefile)
      api=true
      web=true
      ;;
    apps/api/* | infrastructure/docker/pgbouncer/*)
      api=true
      ;;
    apps/web/* | packages/* | package.json | pnpm-lock.yaml | pnpm-workspace.yaml | .npmrc | .nvmrc)
      web=true
      ;;
  esac
done <<<"$files"

echo "api=$api"
echo "web=$web"
