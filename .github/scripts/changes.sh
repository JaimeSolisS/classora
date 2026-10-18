#!/usr/bin/env bash
# Decide which CI job groups a pull request needs (used by the `changes` job in
# .github/workflows/ci.yml). Compares <head> with its merge base on <base>
# (`git diff base...head`) and prints `api=true|false`, `web=true|false`,
# `client=true|false` and `e2e=true|false`.
#
# Run it locally against any two refs:
#   bash .github/scripts/changes.sh <base-ref> <head-ref>
#
# Groups:
#   api: apps/api/**, infrastructure/docker/pgbouncer/** (built by the integration tests)
#   web: apps/web/**, packages/**, package.json, pnpm-lock.yaml,
#        pnpm-workspace.yaml, .npmrc, .nvmrc
#   e2e: apps/**, packages/**, e2e/**, compose.yaml, compose.override.yaml,
#        infrastructure/docker/**, package.json, pnpm-lock.yaml,
#        pnpm-workspace.yaml, .npmrc, .nvmrc, .env.example (everything the
#        E2E Compose stack and the Playwright tests are built from)
#   all (api, web and e2e): .github/workflows/**, .github/scripts/**, Makefile
#   client (the OpenAPI drift check): true when api or web is true. An API
#        change can change the document; a packages/** or lockfile change
#        (generator version) can change the generated client.
# Anything else (docs, README, other infrastructure) runs none of them.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <base-ref> <head-ref>" >&2
  exit 2
fi

files=$(git diff --name-only "$1...$2")

api=false
web=false
e2e=false
while IFS= read -r file; do
  case "$file" in
    .github/workflows/* | .github/scripts/* | Makefile)
      api=true
      web=true
      e2e=true
      ;;
    apps/api/* | infrastructure/docker/pgbouncer/*)
      api=true
      ;;
    apps/web/* | packages/* | package.json | pnpm-lock.yaml | pnpm-workspace.yaml | .npmrc | .nvmrc)
      web=true
      ;;
  esac
  case "$file" in
    apps/* | packages/* | e2e/* | compose.yaml | compose.override.yaml | infrastructure/docker/* | \
      package.json | pnpm-lock.yaml | pnpm-workspace.yaml | .npmrc | .nvmrc | .env.example)
      e2e=true
      ;;
  esac
done <<<"$files"

client=false
if [ "$api" = true ] || [ "$web" = true ]; then
  client=true
fi

echo "api=$api"
echo "web=$web"
echo "client=$client"
echo "e2e=$e2e"
