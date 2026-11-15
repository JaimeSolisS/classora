#!/usr/bin/env bash
# Decide which CI job groups a pull request needs (used by the `changes` job in
# .github/workflows/ci.yml). Compares <head> with its merge base on <base>
# (`git diff base...head`) and prints `api=true|false`, `web=true|false`,
# `client=true|false`, `e2e=true|false`, `images=true|false` and `infra=true|false`.
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
#   images (the arm64 AWS images, #610 E1 (a)): infrastructure/docker/**,
#        apps/api/pyproject.toml, apps/api/uv.lock, package.json,
#        pnpm-lock.yaml, apps/web/package.json, .nvmrc, .dockerignore,
#        apps/web/src/security-headers.ts and
#        apps/web/scripts/print-production-headers.js (the edge's headers).
#        Application code alone doesn't run it: the e2e job's amd64
#        check-pdf-image covers that.
#   infra (configuration checks, #611): infrastructure/observability/**,
#        infrastructure/terraform/**, infrastructure/scripts/**,
#        infrastructure/host/**, compose.yaml (the profile wiring
#        `make check-observability` checks)
#   all (api, web, e2e, images and infra): .github/workflows/**,
#        .github/scripts/**, Makefile
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
images=false
infra=false
while IFS= read -r file; do
  case "$file" in
    .github/workflows/* | .github/scripts/* | Makefile)
      api=true
      web=true
      e2e=true
      images=true
      infra=true
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
  case "$file" in
    infrastructure/docker/* | apps/api/pyproject.toml | apps/api/uv.lock | package.json | pnpm-lock.yaml | \
      apps/web/package.json | .nvmrc | .dockerignore | apps/web/src/security-headers.ts | \
      apps/web/scripts/print-production-headers.js)
      images=true
      ;;
  esac
  case "$file" in
    infrastructure/observability/* | infrastructure/terraform/* | infrastructure/scripts/* | \
      infrastructure/host/* | compose.yaml)
      infra=true
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
echo "images=$images"
echo "infra=$infra"
